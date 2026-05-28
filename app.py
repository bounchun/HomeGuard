import os
import sqlite3
import threading
import time
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify
from dotenv import load_dotenv
from scapy.all import ARP, Ether, srp

load_dotenv()

app = Flask(__name__)

# Config
SCAN_INTERVAL = int(os.getenv("HOMEGUARD_SCAN_INTERVAL", 30))
UNKNOWN_THRESHOLD = int(os.getenv("HOMEGUARD_UNKNOWN_THRESHOLD_SECONDS", 120))
NETWORK_CIDR = os.getenv("HOMEGUARD_NETWORK_CIDR", "192.168.1.0/24")
DEMO_MODE = os.getenv("HOMEGUARD_DEMO_MODE", "false").lower() == "true"
THINGSPEAK_ENABLED = os.getenv("THINGSPEAK_ENABLED", "false").lower() == "true"
THINGSPEAK_CHANNEL_ID = os.getenv("THINGSPEAK_CHANNEL_ID", "")
THINGSPEAK_WRITE_API_KEY = os.getenv("THINGSPEAK_WRITE_API_KEY", "")
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DB_PATH = "homeguard.db"

# Known devices (MAC: label)
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

db_lock = threading.Lock()
camera_lock = threading.Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ip TEXT,
            mac TEXT,
            status TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            mac TEXT,
            ip TEXT,
            snapshot_path TEXT
        )''')
        conn.commit()
        conn.close()

def scan_network():
    if DEMO_MODE:
        return [
            {"ip": "192.168.1.1", "mac": "aa:bb:cc:dd:ee:01"},
            {"ip": "192.168.1.2", "mac": "aa:bb:cc:dd:ee:02"},
        ]
    try:
        arp = ARP(pdst=NETWORK_CIDR)
        ether = Ether(dst="ff:ff:ff:ff:ff:ff")
        packet = ether / arp
        result = srp(packet, timeout=2, verbose=False)[0]
        return [{"ip": r[1].psrc, "mac": r[1].hwsrc} for r in result]
    except Exception as e:
        print(f"Scan error: {e}")
        return []

def send_telegram(message):
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})
    except Exception as e:
        print(f"Telegram error: {e}")

def send_thingspeak(temp, humidity, connected, unknown, alert):
    if not THINGSPEAK_ENABLED:
        return
    try:
        url = "https://api.thingspeak.com/update"
        params = {
            "api_key": THINGSPEAK_WRITE_API_KEY,
            "field1": temp,
            "field2": humidity,
            "field3": connected,
            "field4": unknown,
            "field5": alert,
        }
        requests.get(url, params=params)
    except Exception as e:
        print(f"ThingSpeak error: {e}")


def get_sensehat_readings():
    try:
        import subprocess
        result = subprocess.run(['vcgencmd', 'measure_temp'], capture_output=True, text=True)
        temp_str = result.stdout.strip()
        temp = float(temp_str.replace("temp=", "").replace("'C", ""))
        return round(temp, 1), 0.0
    except Exception as e:
        print(f"Temperature error: {e}")
        return 0.0, 0.0


def trigger_alert(mac, ip):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot_path = None

    # Camera snapshot
    with camera_lock:
        try:
            from picamera2 import Picamera2
            cam = Picamera2()
            config = cam.create_still_configuration()
            cam.configure(config)
            cam.start()
            time.sleep(2)
            snapshot_path = f"static/snapshots/{mac.replace(':','')}.jpg"
            os.makedirs("static/snapshots", exist_ok=True)
            cam.capture_file(snapshot_path)
            cam.stop()
            cam.close()
            print(f"Camera snapshot saved: {snapshot_path}")
        except Exception as e:
            print(f"Camera error: {e}")

    # SenseHAT red flash
    try:
        from sense_hat import SenseHat
        sense = SenseHat()
        sense.clear(255, 0, 0)
        time.sleep(10)
        sense.clear()
        print("SenseHAT flashed red!")
    except Exception as e:
        print(f"SenseHAT error: {e}")

    # Telegram alert
    send_telegram(f"⚠️ Unknown device detected!\nMAC: {mac}\nIP: {ip}\nTime: {timestamp}")

    # Log to DB
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute("INSERT INTO alerts (timestamp, mac, ip, snapshot_path) VALUES (?,?,?,?)",
                  (timestamp, mac, ip, snapshot_path))
        conn.commit()
        conn.close()

unknown_timers = {}

def background_scanner():
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
                status = "known" if mac in KNOWN_DEVICES else "unknown"

                if status == "unknown":
                    unknown_count += 1
                    if mac not in unknown_timers:
                        unknown_timers[mac] = time.time()
                    elif time.time() - unknown_timers[mac] > UNKNOWN_THRESHOLD:
                        threading.Thread(target=trigger_alert, args=(mac, ip), daemon=True).start()
                        unknown_timers.pop(mac)
                else:
                    unknown_timers.pop(mac, None)

                c.execute("INSERT INTO scans (timestamp, ip, mac, status) VALUES (?,?,?,?)",
                          (timestamp, ip, mac, status))

            conn.commit()
            conn.close()

        alert_status = 1 if unknown_count > 0 else 0
        send_thingspeak(temp, humidity, len(devices), unknown_count, alert_status)

        time.sleep(SCAN_INTERVAL)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/devices")
def api_devices():
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
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute("SELECT timestamp, mac, ip, snapshot_path FROM alerts ORDER BY timestamp DESC LIMIT 20")
        rows = c.fetchall()
        conn.close()
    alerts = [{"timestamp": r[0], "mac": r[1], "ip": r[2], "snapshot": r[3]} for r in rows]
    return jsonify(alerts)

if __name__ == "__main__":
    init_db()
    scanner_thread = threading.Thread(target=background_scanner, daemon=True)
    scanner_thread.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
# Feature: network scanning
# Feature: SenseHAT LED
# Feature: camera snapshot
