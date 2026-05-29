import json
import os
import logging
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BROKER   = os.getenv("HIVEMQ_BROKER", "")
PORT     = int(os.getenv("HIVEMQ_PORT", 8883))
USERNAME = os.getenv("HIVEMQ_USER", "")
PASSWORD = os.getenv("HIVEMQ_PASS", "")
TOPIC    = "homeguard/scan"
MQTT_ENABLED = os.getenv("MQTT_ENABLED", "false").lower() == "true"

_client = None

def get_client():
    global _client
    if _client is None:
        try:
            _client = mqtt.Client(client_id="homeguard-pi", protocol=mqtt.MQTTv5)
            _client.username_pw_set(USERNAME, PASSWORD)
            _client.tls_set()
            _client.connect(BROKER, PORT, keepalive=60)
            _client.loop_start()
            logger.info("MQTT connected")
        except Exception as e:
            logger.error(f"MQTT connection error: {e}")
            _client = None
    return _client

def publish_scan(devices, unknown_count, temp):
    if not MQTT_ENABLED:
        return
    try:
        client = get_client()
        if client is None:
            return
        payload = json.dumps({
            "devices": devices,
            "unknown_count": unknown_count,
            "temperature": temp,
        })
        client.publish(TOPIC, payload, qos=1)
        logger.info(f"MQTT published to {TOPIC}")
    except Exception as e:
        logger.error(f"MQTT publish error: {e}")
        global _client
        _client = None
