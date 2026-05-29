import json
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BROKER = os.getenv("HIVEMQ_BROKER", "")
PORT = int(os.getenv("HIVEMQ_PORT", 8883))
USERNAME = os.getenv("HIVEMQ_USER", "")
PASSWORD = os.getenv("HIVEMQ_PASS", "")
TOPIC = "homeguard/scan"
MQTT_ENABLED = os.getenv("MQTT_ENABLED", "false").lower() == "true"

def publish_scan(devices, unknown_count, temp):
    if not MQTT_ENABLED:
        print("MQTT disabled")
        return
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client()
        client.username_pw_set(USERNAME, PASSWORD)
        client.tls_set()
        client.connect(BROKER, PORT, keepalive=60)
        payload = json.dumps({
            "devices": devices,
            "unknown_count": unknown_count,
            "temperature": temp,
        })
        client.publish(TOPIC, payload, qos=1)
        client.disconnect()
        print(f"MQTT published to {TOPIC}")
    except Exception as e:
        print(f"MQTT error: {e}")

