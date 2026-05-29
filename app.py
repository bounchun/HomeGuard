import os
import sqlite3
import threading
import time
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify
from dotenv import load_dotenv
import mqtt_publisher  # MQTT publisher for HiveMQ Cloud
from scapy.all import ARP, Ether, srp

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# ─── Configuration ───────────────────────────────────────────────────────────
# Load all settings from .env file with sensible defaults
SCAN_INTERVAL = int(os.getenv("HOMEGUARD_SCAN_INTERVAL", 30))           # Seconds between ARP scans
UNKNOWN_THRESHOLD = int(os.getenv("HOMEGUARD_UNKNOWN_THRESHOLD_SECONDS", 120))  # Seconds before alert fires
NETWORK_CIDR = os.getenv("HOMEGUARD_NETWORK_CIDR", "192.168.1.0/24")   # Network range to scan
DEMO_MODE = os.getenv("HOMEGUARD_DEMO_MODE", "false").lower() == "true" # Use fake devices for testing
THINGSPEAK_ENABLED = os.getenv("THINGSPEAK_ENABLED", "false").lower() == "true"
THINGSPEAK_CHANNEL_ID = os.getenv("THINGSPEAK_CHANNEL_ID", "")
THINGSPEAK_WRITE_API_KEY = os.getenv("THINGSPEAK_WRITE_API_KEY", "")
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DB_PATH = "homeguard.db"  # SQLite database file path

# ─── Known Devices Whitelist ──────────────────────────────────────────────────
# MAC addresses of trusted devices on the network
# Any device not listed here will be treated as unknown and trigger an alert
KNOWN_DEVICES = {
    "00:e0:20:1e:30:37": "WiFi Repeater",
    "b0:92:4a:b8:73:37": "Main Router",
    "ac:67:84:46:be:81": "Device 1",
    "64:e7:d8:2f:f4:d6": "Device 2",
    "cc:ba:bd:e2:ad:5b": "Device 3",
    "a6:04:03:9f:a0:0b": "Phone/Tablet 1",
    "ae:4a:af:83:c8:ae": "Phone/Tablet 2",
    "0e:6a:c5:9d:91:e0": "Phone/Tablet 3",
}

# Locks to prevent race conditions in multithreaded environment
db_lock = threading.Lock()      # Prevents simultaneous SQLite writes
camera_lock = threading.Lock()  # Prevents simultaneous camera access


# ─── Database Initialisation ──────────────────────────────────────────────────
def init_db():
    """Create the SQLite database and tables if they don't already exist."""
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        # scans table: stores every device seen on each scan cycle
        c.execute('''CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ip TEXT,
            mac TEXT,
            status TEXT
        )''')
        # alerts table: stores unknown device detections with snapshot path
        c.execute('''CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            mac TEXT,
            ip TEXT,
            snapshot_path TEXT
        )''')
        conn.commit()
        conn.close()


# ─── Network Scanner ──────────────────────────────────────────────────────────
def scan_network():
    """
    Perform an ARP scan on the local network to discover connected devices.
    Returns a list of dicts with 'ip' and 'mac' keys.
    In DEMO_MODE, returns fake devices for testing without real hardware.
    """
    if DEMO_MODE:
        return [
            {"ip": "192.168.1.1", "mac": "aa:bb:cc:dd:ee:01"},
            {"ip": "192.168.1.2", "mac": "aa:bb:cc:dd:ee:02"},
        ]
    try:
        # Craft ARP broadcast packet and send it across the network
        arp = ARP(pdst=NETWORK_CIDR)
        ether = Ether(dst="ff:ff:ff:ff:ff:ff")  # Broadcast to all devices
        packet = ether / arp
        result = srp(packet, timeout=2, verbose=False)[0]  # Send and receive responses
        return [{"ip": r[1].psrc, "mac": r[1].hwsrc} for r in result]
    except Exception as e:
        print(f"Scan error: {e}")
        return []


# ─── Telegram Alert ───────────────────────────────────────────────────────────
def send_telegram(message):
    """
    Send a text alert to Telegram using the Bot API.
    Only runs if TELEGRAM_ENABLED=true in .env
    """
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})
    except Exception as e:
        print(f"Telegram error: {e}")


# ─── ThingSpeak Cloud Logging ─────────────────────────────────────────────────
def send_thingspeak(temp, humidity, connected, unknown, alert):
    """
    Send sensor and network data to ThingSpeak cloud via REST API.
    Fields: temp, humidity, connected devices, unknown devices, alert status.
    Only runs if THINGSPEAK_ENABLED=true in .env
    """
    if not THINGSPEAK_ENABLED:
        return
    try:
        url = "https://api.thingspeak.com/update"
        params = {
            "api_key": THINGSPEAK_WRITE_API_KEY,
            "field1": temp,      # CPU temperature (vcgencmd workaround)
            "field2": humidity,  # Humidity (0.0 — SenseHAT sensor issue)
            "field3": connected, # Total devices on network
            "field4": unknown,   # Unknown/unrecognised devices
            "field5": alert,     # 1 = alert active, 0 = all clear
        }
        requests.get(url, params=params)
    except Exception as e:
        print(f"ThingSpeak error: {e}")


# ─── Temperature Reading ──────────────────────────────────────────────────────
def get_sensehat_readings():
    """
    Read CPU temperature using vcgencmd as a workaround for the SenseHAT
    humidity/temperature sensor I2C issue.
    Returns (temperature, humidity) tuple — humidity is always 0.0.
    """
    try:
        import subprocess
        result = subprocess.run(['vcgencmd', 'measure_temp'], capture_output=True, text=True)
        temp_str = result.stdout.strip()
        temp = float(temp_str.replace("temp=", "").replace("'C", ""))
        return round(temp, 1), 0.0
    except Exception as e:
        print(f"Temperature error: {e}")
        return 0.0, 0.0


# ─── Alert Handler ────────────────────────────────────────────────────────────
def trigger_alert(mac, ip):
    """
    Triggered when an unknown device has been present for longer than
    UNKNOWN_THRESHOLD seconds. Runs in a separate thread to avoid
    blocking the main scanner loop.
    Actions: camera snapshot, SenseHAT red flash, Telegram alert, DB log.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot_path = None

    # Camera snapshot — lock prevents simultaneous captures
    with camera_lock:
        try:
            from picamera2 import Picamera2
            cam = Picamera2()
            config = cam.create_still_configuration()
            cam.configure(config)
            cam.start()
            time.sleep(2)  # Allow camera to warm up
            snapshot_path = f"static/snapshots/{mac.replace(':','')}.jpg"
            os.makedirs("static/snapshots", exist_ok=True)
            cam.capture_file(snapshot_path)
            cam.stop()
            cam.close()
            print(f"Camera snapshot saved: {snapshot_path}")
        except Exception as e:
            print(f"Camera error: {e}")

    # SenseHAT red flash — visual indicator on the Pi itself
    try:
        from sense_hat import SenseHat
        sense = SenseHat()
        sense.clear(255, 0, 0)  # Flash red
        time.sleep(10)
        sense.clear()           # Turn off after 10 seconds
        print("SenseHAT flashed red!")
    except Exception as e:
        print(f"SenseHAT error: {e}")

    # Send Telegram notification with device details
    send_telegram(f"⚠️ Unknown device detected!\nMAC: {mac}\nIP: {ip}\nTime: {timestamp}")

    # Log alert to SQLite database
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute("INSERT INTO alerts (timestamp, mac, ip, snapshot_path) VALUES (?,?,?,?)",
                  (timestamp, mac, ip, snapshot_path))
        conn.commit()
        conn.close()


# ─── Background Scanner Thread ────────────────────────────────────────────────
# Tracks how long each unknown MAC has been seen continuously
unknown_timers = {}

def background_scanner():
    """
    Main scanning loop — runs in a background daemon thread.
    Every SCAN_INTERVAL seconds:
      1. ARP scan the network
      2. Classify each device as known or unknown
      3. Trigger alert if unknown device exceeds threshold
      4. Log all devices to SQLite
      5. Send data to ThingSpeak (REST) and HiveMQ (MQTT)
    """
    while True:
        devices = scan_network()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        temp, humidity = get_sensehat_readings()
        unknown_count = 0

        with db_lock:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()

            for device in devices:
                mac = device["mac"]
                ip = device["ip"]
                # Check if MAC is in the whitelist
                status = "known" if mac in KNOWN_DEVICES else "unknown"

                if status == "unknown":
                    unknown_count += 1
                    if mac not in unknown_timers:
                        # First time seeing this unknown device — start the timer
                        unknown_timers[mac] = time.time()
                    elif time.time() - unknown_timers[mac] > UNKNOWN_THRESHOLD:
                        # Device has been unknown for too long — trigger alert
                        threading.Thread(target=trigger_alert, args=(mac, ip), daemon=True).start()
                        unknown_timers.pop(mac)  # Reset timer after alert
                else:
                    # Known device — remove from unknown timers if present
                    unknown_timers.pop(mac, None)

                # Log every device seen in this scan cycle
                c.execute("INSERT INTO scans (timestamp, ip, mac, status) VALUES (?,?,?,?)",
                          (timestamp, ip, mac, status))

            conn.commit()
            conn.close()

        alert_status = 1 if unknown_count > 0 else 0

        # Send data via REST to ThingSpeak cloud
        send_thingspeak(temp, humidity, len(devices), unknown_count, alert_status)

        # Send scan results via MQTT to HiveMQ Cloud (second communication method)
        mqtt_publisher.publish_scan(devices, unknown_count, temp)

        # Wait before next scan cycle
        time.sleep(SCAN_INTERVAL)


# ─── Flask Routes ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    """Serve the main web dashboard."""
    return render_template("index.html")


@app.route("/api/devices")
def api_devices():
    """
    REST API endpoint — returns the latest status of all devices seen.
    Groups by MAC address and returns the most recent scan entry for each.
    """
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute('''SELECT ip, mac, status, MAX(timestamp) as last_seen
                     FROM scans GROUP BY mac ORDER BY last_seen DESC''')
        rows = c.fetchall()
        conn.close()
    devices = [{"ip": r[0], "mac": r[1], "status": r[2], "last_seen": r[3]} for r in rows]
    return jsonify(devices)


@app.route("/api/alerts")
def api_alerts():
    """
    REST API endpoint — returns the 20 most recent alert events.
    Includes MAC, IP, timestamp, and snapshot path for each alert.
    """
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute("SELECT timestamp, mac, ip, snapshot_path FROM alerts ORDER BY timestamp DESC LIMIT 20")
        rows = c.fetchall()
        conn.close()
    alerts = [{"timestamp": r[0], "mac": r[1], "ip": r[2], "snapshot": r[3]} for r in rows]
    return jsonify(alerts)


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()  # Create DB tables if they don't exist
    # Start background scanner in a daemon thread (stops when main app stops)
    scanner_thread = threading.Thread(target=background_scanner, daemon=True)
    scanner_thread.start()
    # Start Flask web server on all interfaces, port 5000
    app.run(host="0.0.0.0", port=5000, debug=False)
