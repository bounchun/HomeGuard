# HomeGuard 🏠

A Raspberry Pi-based home network monitoring system that detects unknown devices, captures snapshots, and sends real-time alerts.

## Features
- 📡 Scans home network every 30 seconds using ARP
- 🔍 Detects and flags unknown MAC addresses
- 📸 Captures camera snapshot when unknown device detected
- 🔴 Flashes SenseHAT LED matrix red for 10 seconds on alert
- 📱 Sends Telegram notification to your phone
- 🌡️ Logs CPU temperature to ThingSpeak cloud
- 🗄️ Stores all events in SQLite database
- 🌐 Flask web dashboard accessible on local network
- ⚙️ Auto-starts on boot via systemd

## Hardware Required
- Raspberry Pi 4
- Raspberry Pi SenseHAT
- Raspberry Pi Camera Module (IMX708)
- MicroSD card and power supply
- Home WiFi network

## Software Stack
| Component | Technology |
|-----------|------------|
| Web Framework | Flask (Python) |
| Database | SQLite |
| Network Scanning | Scapy (ARP) |
| Cloud Logging | ThingSpeak REST API |
| Alerts | Telegram Bot API |
| Hardware | SenseHAT, Picamera2 |
| OS Service | systemd |

## Quick Start
```bash
git clone https://github.com/your-username/HomeGuard.git
cd HomeGuard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
sudo python3 app.py
```
Access dashboard at `http://<raspberry-pi-ip>:5000`

## Project Structure

HomeGuard/
├── app.py              # Main Flask application
├── requirements.txt    # Python dependencies
├── .env.example        # Example config
├── templates/
│   └── index.html      # Web dashboard
├── static/
│   └── snapshots/      # Camera snapshots
└── docs/
└── installation_Guide.docx

## IoT Architecture

[Home Network Devices]
↓ ARP Scan
[Raspberry Pi 4]
↓              ↓              ↓
[Flask Dashboard] [ThingSpeak] [Telegram Bot]
↓
[SQLite Database]

## Known Issues
- SenseHAT humidity/temperature sensor not detected (I2C issue)
- Workaround: CPU temperature used via vcgencmd measure_temp
- See docs/installation_Guide.docx for full details

## Author
HDip Computer Science — IoT Project 2026
