import json
import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Logger for this module — output appears in journalctl logs
logger = logging.getLogger(__name__)

# ─── HiveMQ Cloud Configuration ───────────────────────────────────────────────
# All credentials loaded from .env — never hardcoded
BROKER   = os.getenv("HIVEMQ_BROKER", "")   # e.g. abc123.s1.eu.hivemq.cloud
PORT     = int(os.getenv("HIVEMQ_PORT", 8883))  # 8883 = MQTT over TLS
USERNAME = os.getenv("HIVEMQ_USER", "")
PASSWORD = os.getenv("HIVEMQ_PASS", "")

# MQTT topic that HomeGuard publishes scan results to
TOPIC = "homeguard/scan"

# Toggle MQTT on/off without changing code — set MQTT_ENABLED=true in .env
MQTT_ENABLED = os.getenv("MQTT_ENABLED", "false").lower() == "true"


# ─── MQTT Publisher ───────────────────────────────────────────────────────────
def publish_scan(devices, unknown_count, temp):
    """
    Publish a scan event to HiveMQ Cloud via MQTT over TLS.
    Called by background_scanner() in app.py on every scan cycle,
    alongside the existing REST calls to ThingSpeak and Telegram.

    Args:
        devices       -- list of dicts with 'ip' and 'mac' keys
        unknown_count -- number of unrecognised devices in this scan
        temp          -- CPU temperature reading from vcgencmd
    """
    # Skip publishing if MQTT is disabled in .env
    if not MQTT_ENABLED:
        print("MQTT disabled")
        return

    try:
        import paho.mqtt.client as mqtt

        # Create a new MQTT client instance for this publish
        client = mqtt.Client()

        # Set HiveMQ credentials for authentication
        client.username_pw_set(USERNAME, PASSWORD)

        # Enable TLS encryption — required by HiveMQ Cloud (port 8883)
        client.tls_set()

        # Connect to the HiveMQ Cloud broker
        client.connect(BROKER, PORT, keepalive=60)

        # Build the JSON payload with scan results
        payload = json.dumps({
            "devices": devices,           # All devices seen on this scan
            "unknown_count": unknown_count, # How many were unrecognised
            "temperature": temp,           # Pi CPU temperature
        })

        # Publish to the homeguard/scan topic with QoS 1 (at least once delivery)
        client.publish(TOPIC, payload, qos=1)

        # Disconnect cleanly after publishing
        client.disconnect()

        print(f"MQTT published to {TOPIC}")

    except Exception as e:
        # Log error but don't crash the main scanner loop
        print(f"MQTT error: {e}")
