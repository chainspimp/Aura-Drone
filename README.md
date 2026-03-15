# AURA Drone — Autonomous Unified Reconnaissance Assistant

> **A fully autonomous AI survival drone. No internet. No cloud. No subscription. Just you, the sky, and AURA.**

AURA is a Python-based autonomous drone system built around a Pixhawk 6C flight controller and an NVIDIA Jetson Orin Nano companion computer. All AI inference runs 100% locally via Ollama. Designed for SHTF (disaster survival), off-grid security, and austere-environment reconnaissance.

---

## Table of Contents

1. [Features](#features)
2. [Full Parts List](#full-parts-list)
3. [Cost Estimates](#cost-estimates)
4. [Software Installation](#software-installation)
5. [Wiring Diagram](#wiring-diagram)
6. [ArduPilot Parameters](#ardupilot-parameters)
7. [First Flight Checklist](#first-flight-checklist)
8. [Voice Command Reference](#voice-command-reference)
9. [Troubleshooting](#troubleshooting)
10. [Legal & Safety Warnings](#legal--safety-warnings)
11. [Roadmap](#roadmap)

---

## Features

### 🎯 Threat Detection
- **YOLOv8 real-time detection** — persons, vehicles, boats, and gear at up to 60fps (TensorRT on Jetson)
- **FLIR Lepton 3.5 thermal** — detects human heat signatures day or night, through smoke and camouflage
- **Face recognition** — identifies known group members vs unknown contacts, fires alert on strangers
- **Threat scoring** — classifies detections into NONE/LOW/MEDIUM/HIGH/CRITICAL with operator recommendations
- **Perimeter patrol** — continuous autonomous loop with dwell-and-scan at each waypoint

### 🗺️ Reconnaissance
- **Corridor scout** — systematic S-pattern scan of a route, photos every 10m, AI-generated threat report
- **Building assessment** — multi-altitude orbital scan with structural hazard analysis
- **Resource detection** — aerial detection of water sources, crops, supply caches from color analysis + YOLO
- **Situation map** — Folium HTML interactive map with all geo-tagged detections, flight path, markers

### 📡 Communications
- **WiFi relay** — broadcasts `AURA-RELAY` WPA2 hotspot from 60m altitude, extending ground team range
- **LoRa 915MHz bridge** — AES-128 encrypted text messaging between camps, 15km+ range, retries queue
- **Message courier** — physically flies messages to out-of-range camps and hovers for pickup

### 🧠 Local AI (Zero Internet)
- **Command parsing** — natural language voice/text commands via `gemma3n:e2b`
- **Mission planning** — multi-step mission generation from goals via `deepseek-r1:8b`
- **Scene description** — "what do you see?" visual queries via `qwen2.5-vl:7b`
- **Situation reports** — real-time SITREP synthesis from all sensor data
- **Graceful degradation** — all AI features fall back to rule-based logic if Ollama is down

### 🎙️ Voice Control
- **Always-on wake word** — "Hey AURA" activates command mode
- **Vosk offline STT** — no internet speech recognition on-device
- **Piper TTS** — natural-sounding spoken alerts from the onboard speaker

### 🔋 Safety Systems
- **Battery monitor thread** — independent of all other systems, forces RTH/land at critical voltage
- **GPS loss handler** — LOITER then forced landing if GPS doesn't recover in 30 seconds
- **Comms loss watchdog** — auto-RTH after 30s GCS link loss
- **Payload clearance check** — YOLO zone scan before payload release

---

## Full Parts List

### 🪁 Frame & Motors

| Component | Model | Specs | Price | Where |
|-----------|-------|-------|-------|-------|
| Frame | DJI F450 / Tarot 650 / Custom Carbon 5" | 450–650mm, 1.5mm carbon arms | $25–$80 | GetFPV, Amazon |
| Motors (×4) | T-Motor MN3110-17 KV700 | 700KV, 6S compatible, 220W each | $35 each | T-Motor Direct |
| ESCs (×4) | BLHeli32 45A 3–6S | 45A continuous, BLHeli32 firmware | $18 each | Amazon, Banggood |
| Props (×4 pairs) | 15×5.0 Carbon Fiber | 15" diameter, CW/CCW pair | $12/pair | Amazon |
| Prop guards (optional) | Generic 15" guards | Safety for close proximity | $20 | Amazon |

### ⚡ Power System

| Component | Model | Specs | Price | Where |
|-----------|-------|-------|-------|-------|
| Battery | Tattu 6S 10000mAh 25C | 22.2V nominal, 150A burst | $110 | Tattu Direct, GetFPV |
| Battery (backup) | Tattu 6S 10000mAh 25C | Identical (buy 2 for longer ops) | $110 | Tattu Direct |
| Power Distribution Board | Matek FCHUB-6S | 6S rated, 5V/12V BECs included | $22 | Matek, Amazon |
| XT60 connectors (×4) | Genuine XT60 Male/Female | For battery leads | $8/10pk | Amazon |
| Power module | Holybro PM07 | 90A continuous, 5.3V/3A BEC | $25 | Holybro Direct |

### 🎮 Flight Controller

| Component | Model | Specs | Price | Where |
|-----------|-------|-------|-------|-------|
| Flight Controller | Pixhawk 6C Mini | STM32H7, ArduCopter, TELEM2 @ 921600 | $180 | Holybro Direct |
| GPS Module | Holybro M9N | u-blox M9N, 25Hz, <1m CEP | $65 | Holybro Direct |
| GPS Mast | 30cm carbon mast | Gets GPS away from FC interference | $8 | Amazon |
| Safety Switch | Included with Pixhawk | — | — | — |
| Buzzer | Included with Pixhawk | — | — | — |

### 🖥️ Companion Computer

| Component | Model | Specs | Price | Where |
|-----------|-------|-------|-------|-------|
| Companion Computer | NVIDIA Jetson Orin Nano 8GB | 8GB RAM, 1024 CUDA cores, 40 TOPS | $499 | NVIDIA/Arrow |
| Dev Kit Carrier Board | Official Jetson Orin Nano Dev Kit | Includes all I/O | Included | NVIDIA |
| microSD Card | Samsung Pro Endurance 256GB | For OS + logs + models | $35 | Amazon |
| NVMe SSD (optional) | WD SN550 500GB M.2 2280 | Faster model loading | $50 | Amazon |
| Heatsink | JETSON-Orin-Nano heatsink | Passive cooling (active not needed at 30m) | $15 | Amazon |

### 📷 Cameras & Sensors

| Component | Model | Specs | Price | Where |
|-----------|-------|-------|-------|-------|
| Main Camera | ArduCam IMX477 HQ USB | 12.3MP, 4056×3040, USB 3.0 | $65 | ArduCam Direct |
| HQ Camera Lens | 6mm CS-Mount Lens | 63° FoV, CS mount | $25 | ArduCam, Amazon |
| Thermal Camera | FLIR Lepton 3.5 | 160×120, 8–14μm LWIR, radiometric | $200 | FLIR/DigiKey |
| Thermal USB Board | GetThermal PureThermal 2 | Lepton module carrier, USB UVC | $99 | GroupGets |
| Downward Camera | ArduCam OV5647 CSI | 5MP, CSI-2, optical flow/landing | $20 | ArduCam, Amazon |
| Rangefinder | TFmini-S LiDAR | 12m range, 100Hz, UART | $35 | Amazon |

### 📻 Communications

| Component | Model | Specs | Price | Where |
|-----------|-------|-------|-------|-------|
| WiFi Adapter | Alfa AWUS036ACM | AC1200, 2.4/5GHz, AP mode support | $45 | Amazon |
| WiFi Antenna | Alfa ARS-N19 | 9dBi directional, RP-SMA | $18 | Amazon |
| LoRa Radio | RYLR998 × 2 | 915MHz, 22dBm, UART AT commands | $28 each | Amazon |
| LoRa Antenna | SMA 915MHz 3dBi Whip | Matched to RYLR998 | $8 each | Amazon |
| Telemetry Radio (optional) | RFD900x × 2 | 915MHz, 1W, MAVLink, 40km LOS | $145/pair | RFDesign |

### 🔊 Audio

| Component | Model | Specs | Price | Where |
|-----------|-------|-------|-------|-------|
| I2S Amplifier | MAX98357A breakout | 3W, I2S, 3.3V/5V | $6 | SparkFun, Amazon |
| Speaker | Visaton FRS 5 | 3W, 8Ω, 50mm | $8 | Amazon |
| Microphone | MEMS I2S Mic (SPH0645) | Omnidirectional, I2S, -26dBFS | $8 | Adafruit |

### 🦾 Gimbal & Payload

| Component | Model | Specs | Price | Where |
|-----------|-------|-------|-------|-------|
| Camera Gimbal | Tarot T2-2D | 2-axis brushless, 360° pan, PWM/SBUS | $85 | Amazon, AliExpress |
| Payload Bay | DIY 3D printed or servo tray | Custom to your frame | $5 mat. | — |
| Payload Servo | Tower Pro SG90 | 9g micro servo, PWM | $5 | Amazon |

### 🔌 Wiring & Misc

| Component | Model | Specs | Price | Where |
|-----------|-------|-------|-------|-------|
| Serial-to-USB adapter | CP2102 module | 3.3V UART, for LoRa | $5 | Amazon |
| Vibration dampers | M3 Nylon standoffs | FC vibration isolation | $6 | Amazon |
| Heat shrink assortment | Assorted sizes | Wiring protection | $8 | Amazon |
| JST connectors | JST-GH 6-pin | Pixhawk telemetry cable | $10 | Amazon |
| Cable ties | 150mm nylon | Frame cable management | $5 | Amazon |
| Kapton tape | High-temp polyimide tape | FC mounting | $6 | Amazon |

---

## Cost Estimates

| Build Tier | Description | Est. Total |
|------------|-------------|-----------|
| **Full Build** | All components as listed | **~$1,800** |
| **Budget Build** | Substitute Raspberry Pi 4 for Jetson, skip thermal, basic WiFi adapter | **~$850** |
| **Minimum Viable** | FC + basic frame + RPi4 + USB cam, no thermal/gimbal | **~$450** |

> **Note:** The Jetson Orin Nano alone is $499. It's the biggest single cost but provides the CUDA cores needed for real-time YOLOv8 at useful FPS with thermal + voice running simultaneously.

---

## Software Installation

### Step 1 — Flash JetPack

```bash
# Download NVIDIA SDK Manager from developer.nvidia.com/sdk-manager
# Flash JetPack 5.1.3 (Ubuntu 22.04 + CUDA + TensorRT)
# Follow NVIDIA's official guide for your carrier board
```

### Step 2 — System Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install system dependencies
sudo apt install -y \
    python3-pip python3-tk python3-dev \
    portaudio19-dev espeak alsa-utils aplay \
    hostapd dnsmasq \
    libatlas-base-dev libopenblas-dev \
    cmake build-essential libboost-all-dev \
    git curl wget \
    v4l-utils \
    libbluetooth-dev

# Allow Jetson to run at max performance
sudo nvpmodel -m 0
sudo jetson_clocks
```

### Step 3 — Install Ollama

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama service
sudo systemctl enable ollama
sudo systemctl start ollama

# Pull required models (this will take 30-60 minutes depending on connection)
ollama pull gemma3n:e2b         # ~1.5GB — fast command parsing
ollama pull deepseek-r1:8b      # ~4.7GB — mission planning
ollama pull qwen2.5-vl:7b       # ~4.4GB — vision descriptions

# Verify models are loaded
ollama list
```

### Step 4 — Clone & Configure AURA

```bash
# Clone project
git clone https://github.com/your-repo/aura_drone.git
cd aura_drone

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install pylepton from source (Jetson FLIR driver)
git clone https://github.com/groupgets/pylepton.git
cd pylepton && pip install . && cd ..

# Copy environment config
cp .env.example .env
nano .env   # Edit with your values (serial ports, WiFi password, LoRa key)
```

### Step 5 — Install Vosk Speech Model

```bash
# Download Vosk English small model (~50MB)
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
# Model directory should now exist at ./vosk-model-small-en-us-0.15/
```

### Step 6 — Install Piper TTS

```bash
# Download Piper binary for ARM64 (Jetson)
wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_aarch64.tar.gz
tar -xzf piper_linux_aarch64.tar.gz
sudo mv piper/piper /usr/local/bin/
sudo chmod +x /usr/local/bin/piper

# Download voice model
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_female/medium/en_US-hfc_female-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_female/medium/en_US-hfc_female-medium.onnx.json
# Move to config-specified location or update PIPER_MODEL in .env
```

### Step 7 — TensorRT YOLO Acceleration (Optional but Recommended)

```bash
# Export YOLOv8n to TensorRT engine (takes ~10 minutes first run)
# Run this once, then AURA will auto-use the .engine file
python3 -c "
from ultralytics import YOLO
model = YOLO('yolov8n.pt')
model.export(format='engine', device=0, half=True, imgsz=640)
print('Engine exported to yolov8n.engine')
"
```

### Step 8 — Add Known Faces

```bash
# Add photos of your group to the known_faces/ directory
# Filename format: FirstName_LastName.jpg
cp /path/to/alice_photo.jpg known_faces/Alice_Smith.jpg
cp /path/to/bob_photo.jpg known_faces/Bob_Jones.jpg
# One clear, front-facing photo per person minimum
# Multiple photos per person: Alice_Smith_1.jpg, Alice_Smith_2.jpg, etc.
```

### Step 9 — Configure ArduPilot (see next section)

### Step 10 — Run AURA

```bash
# Activate venv
source venv/bin/activate

# Run with GUI (normal operation)
python3 drone_main.py

# Run headless (no display, e.g. SSH session)
python3 drone_main.py --no-gui

# Run with SITL simulator (no hardware needed for testing)
python3 drone_main.py --sim

# Run on boot (systemd service)
sudo cp scripts/aura_drone.service /etc/systemd/system/
sudo systemctl enable aura_drone
sudo systemctl start aura_drone
```

---

## Wiring Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AURA DRONE WIRING DIAGRAM                           │
│                           Pixhawk 6C Mini + Jetson Orin Nano                │
└─────────────────────────────────────────────────────────────────────────────┘

6S LiPo Battery (22.2V)
│
├─── XT60 ──► Power Distribution Board (Matek FCHUB-6S)
│              │
│              ├─── 4× ESC (BLHeli32 45A)
│              │     └─── 4× Motor (T-Motor MN3110)
│              │
│              ├─── 12V BEC ──► Jetson Orin Nano (via 12V barrel jack)
│              │
│              ├─── 5V BEC ──► Holybro PM07 ──► Pixhawk 6C Mini (POWER1)
│              │
│              └─── 5V BEC ──► MAX98357A amp (VDD)
│
├─── PM07 ──► Pixhawk 6C Mini (POWER1)    [voltage/current sensing]

Pixhawk 6C Mini:
│
├─── TELEM2 (JST-GH 6-pin) ──► UART-USB (CP2102) ──► Jetson USB-A
│     [921600 baud, MAVLink — DroneKit connection]
│
├─── GPS1 ──► Holybro M9N GPS Module
│     [UART + I2C: GPS + compass]
│
├─── I2C ──► (compass on M9N, shared bus)
│
├─── RC IN ──► RC Receiver (if using RC failsafe)
│
├─── AUX OUT 1 (AUX1) ──► Tarot T2-2D Gimbal (PWM signal)
│
├─── AUX OUT 2 (AUX2) ──► SG90 Payload Servo (PWM signal)
│     [controlled via MAVLink DO_SET_SERVO]
│
├─── MOTOR OUT 1-4 ──► ESC signal wires (BLHeli32)
│
└─── BUZZER / SAFETY SWITCH ──► Included accessories

Jetson Orin Nano:
│
├─── USB-A (port 1) ──► ArduCam IMX477 HQ (main camera, USB 3.0)
│
├─── USB-A (port 2) ──► PureThermal 2 + FLIR Lepton 3.5 (USB UVC)
│
├─── USB-A (port 3) ──► CP2102 UART adapter ──► Pixhawk TELEM2
│
├─── USB-A (port 4) ──► Alfa AWUS036ACM WiFi (via USB 3.0)
│
├─── CSI0 ──► ArduCam OV5647 (downward camera, CSI-2)
│
├─── GPIO (UART0 / ttyS0):
│     TX ──► RYLR998 RX pin
│     RX ──► RYLR998 TX pin
│     GND ──► RYLR998 GND
│     3.3V ──► RYLR998 VDD
│
├─── I2S (GPIO pins 12,35,38,40):
│     BCK  ──► MAX98357A BCLK
│     LRCK ──► MAX98357A LRC
│     DIN  ──► MAX98357A DIN
│     3.3V ──► MAX98357A SD (shutdown — pull high to enable)
│     GND  ──► MAX98357A GND
│     │
│     └── MAX98357A OUT+ / OUT- ──► Speaker (3W 8Ω)
│
└─── I2S MIC (SPH0645):
      SCK ──► GPIO (I2S CLK)
      WS  ──► GPIO (I2S WS)
      SD  ──► GPIO (I2S DATA)
      VDD ──► 3.3V
      GND ──► GND
      SEL ──► GND (left channel)

RYLR998 LoRa Module:
├─── VDD ──► 3.3V (Jetson GPIO)
├─── GND ──► GND
├─── TX  ──► Jetson UART RX (GPIO10 / ttyS0 RX)
├─── RX  ──► Jetson UART TX (GPIO8  / ttyS0 TX)
└─── ANT ──► 915MHz SMA antenna

Power Budget (approximate):
  Jetson Orin Nano:   7–15W (idle–full load)
  Pixhawk 6C Mini:    0.5W
  Cameras (3×):       3W total
  Thermal + USB:      1W
  WiFi adapter:       3W
  LoRa module:        0.5W
  Speaker + amp:      3W peak
  Total electronics:  ~28W max
  Motors at hover:    ~120W (6S × 6A hover current)
  Total system:       ~150W at hover
  Flight time (10Ah): ~30–35 minutes at normal operation
```

---

## ArduPilot Parameters

Connect to Pixhawk via Mission Planner or QGroundControl and set these parameters:

### Required Parameters

```
# Serial port for companion computer
SERIAL2_BAUD      921600    # TELEM2 baud rate for Jetson connection
SERIAL2_PROTOCOL  2         # MAVLink 2.0

# Failsafe settings
FS_BATT_ENABLE    1         # Enable battery failsafe
FS_BATT_VOLTAGE   19.8      # Critical voltage (3.3V/cell × 6S)
FS_BATT_MAH       0         # Disable mAh-based failsafe (use voltage)
FS_GCS_ENABLE     1         # Enable GCS heartbeat failsafe
FS_GCS_TIMEOUT    5         # Seconds before GCS failsafe triggers

# Guided mode for companion computer control
GUID_TIMEOUT      10        # GUIDED mode timeout before falling back

# Gimbal (Tarot T2-2D on AUX1)
MNT_TYPE          1         # Servo gimbal
MNT_RC_IN_TILT   0         # Tilt controlled by autopilot, not RC
MNT_ANGMIN_TIL   -9000     # Minimum tilt (-90°, straight down)
MNT_ANGMAX_TIL   0          # Maximum tilt (0°, forward)
SERVO9_FUNCTION   7         # AUX1 = mount tilt

# Payload servo (SG90 on AUX2)
SERVO10_FUNCTION  0         # AUX2 = manual servo (DO_SET_SERVO)
SERVO10_MIN       1000      # Closed position
SERVO10_MAX       2000      # Open position
SERVO10_TRIM      1000      # Default to closed

# GPS
GPS_TYPE         1           # u-blox auto-detect
GPS_GNSS_MODE    0           # All constellations
GPS_HDOP_GOOD    1.3         # HDOP threshold for EKF arming

# EKF
AHRS_EKF_TYPE    3           # EKF3
EK3_ENABLE       1

# Barometer
BARO_EXTERNAL_BUS 0         # External I2C baro if fitted

# Logging (to Jetson via MAVLink)
LOG_BACKEND_TYPE  2          # MAVLink logging to companion
LOG_BITMASK       65535       # Log everything

# Rangefinder (TFmini-S on SERIAL4)
RNGFND1_TYPE     20          # Benewake TFmini
RNGFND1_ORIENT   25          # Downward-facing
RNGFND1_MIN_CM   30
RNGFND1_MAX_CM   1200
```

### Recommended Tuning Starting Points (450mm frame)

```
# PID - these will need tuning for your specific frame
ATC_RAT_RLL_P    0.135
ATC_RAT_RLL_I    0.135
ATC_RAT_RLL_D    0.0036
ATC_RAT_PIT_P    0.135
ATC_RAT_PIT_I    0.135
ATC_RAT_PIT_D    0.0036
ATC_RAT_YAW_P    0.18
ATC_RAT_YAW_I    0.018

# Speed limits
WPNAV_SPEED      500         # 5 m/s waypoint speed (500 = cm/s)
WPNAV_SPEED_UP   250         # 2.5 m/s climb
WPNAV_SPEED_DN   150         # 1.5 m/s descent
LAND_SPEED       50          # 0.5 m/s final descent
```

---

## First Flight Checklist

### Pre-Flight (Every Flight)

- [ ] Battery fully charged (>95%, voltage >25.0V)
- [ ] Battery physically secure in frame, XT60 connector tight
- [ ] All four propellers tight, correct rotation direction (CW/CCW)
- [ ] Camera lenses clean, gimbal moves freely
- [ ] LoRa antennas attached, all USB devices connected
- [ ] WiFi adapter visible in `ip link show`
- [ ] RYLR998 powered (`AT` command returns `+OK` in console)
- [ ] GPS has 3D fix (LED indicator on M9N, or check QGC)
- [ ] ArduPilot pre-arm checks all green (listen for disarm tone)

### Software Pre-Flight

```bash
# Verify Ollama models loaded
ollama list

# Verify cameras visible
v4l2-ctl --list-devices

# Test YOLO with one frame
python3 -c "from vision.yolo_watch import YOLOWatcher; w = YOLOWatcher(); print('YOLO OK')"

# Test MAVLink connection (with USB connected to Pixhawk)
python3 -c "
import sys; sys.path.insert(0,'.')
from flight.drone_control import DroneController
d = DroneController()
if d.connect('/dev/ttyUSB0', 921600):
    print('MAVLink OK:', d.get_telemetry())
"

# Start AURA in sim mode to verify all imports work
python3 drone_main.py --sim --no-gui
# Wait for 'AURA online' message, then Ctrl-C
```

### First Outdoor Flight

1. Take to an open area (100m+ from people and structures)
2. Set home point by disarming and re-arming in place
3. Manual test flight first (no AURA): switch to STABILIZE, hover at 2m for 1 min
4. Test GUIDED mode from RC: switch to GUIDED, ensure drone holds position
5. Connect Jetson, run `python3 drone_main.py`
6. Test voice: "Hey AURA" → "takeoff 10" (10 meter test altitude)
7. Test hover: "Hey AURA" → "hover"
8. Test RTH: "Hey AURA" → "return home"
9. Verify landing and auto-disarm

---

## Voice Command Reference

| Command | What AURA Does |
|---------|----------------|
| `Hey AURA` | Activates voice command mode (wake word) |
| `takeoff` | Arms and ascends to PATROL_ALTITUDE_M (30m default) |
| `takeoff 50` | Arms and ascends to 50 meters |
| `land` | Switches to LAND mode |
| `return home` | Switches to RTL mode |
| `hover` | Switches to LOITER mode (holds position) |
| `patrol` | Begins perimeter patrol on last loaded waypoints |
| `stop patrol` | Halts patrol, switches to hover |
| `scout north` | Scouts ahead in current heading direction |
| `what do you see` | Captures frame, asks vision AI to describe scene |
| `situation report` | Generates full SITREP from all sensors |
| `drop payload` | Checks zone clearance, releases payload via servo |
| `drop payload now` | Force-releases payload (skips zone check) |
| `relay mode` | Ascends to relay altitude and activates WiFi AP |
| `orbit here` | Circles current position at 20m radius |
| `set gimbal down` | Points camera straight down (mapping/nadir) |
| `set gimbal forward` | Points camera forward |
| `send message [text]` | Broadcasts text via LoRa to all units |

---

## Troubleshooting

### "MAVLink connection failed"
```bash
# Check USB connection
ls /dev/ttyUSB*
# Should show /dev/ttyUSB0 when Pixhawk connected

# Add user to dialout group (persistent serial access)
sudo usermod -a -G dialout $USER
# Log out and log back in

# Test raw serial
screen /dev/ttyUSB0 921600
# Should see MAVLink binary data (garbled text = working)
# Ctrl-A then K to exit
```

### "YOLO camera not found"
```bash
# List V4L2 video devices
v4l2-ctl --list-devices

# Test camera directly
python3 -c "import cv2; cap=cv2.VideoCapture(0); print(cap.isOpened())"

# If using CSI camera on Jetson, check GStreamer pipeline
nvgstcapture-1.0 --sensor-id=0  # Test CSI camera

# USB camera permissions
sudo chmod 777 /dev/video0
```

### "Thermal camera not detected"
```bash
# PureThermal should appear as a V4L2 device when plugged in
v4l2-ctl --list-devices | grep -i lepton

# Check dmesg for USB enumeration
dmesg | tail -20 | grep -i usb

# Test V4L2 capture directly
python3 -c "
import cv2
cap = cv2.VideoCapture(2)  # Try IDs 2,3,4 if not device 2
ret, frame = cap.read()
print(f'Thermal frame: {ret}, shape: {frame.shape if ret else None}')
"
```

### "Ollama not responding"
```bash
# Check Ollama service status
sudo systemctl status ollama

# Restart Ollama
sudo systemctl restart ollama

# Test API directly
curl http://localhost:11434/api/tags

# Check if models are downloaded
ollama list

# Pull missing model
ollama pull gemma3n:e2b

# Check Ollama port isn't blocked
ss -tlnp | grep 11434
```

### "Voice recognition not working"
```bash
# Verify microphone is detected
arecord -l

# Test recording
arecord -D default -r 16000 -f S16_LE -d 3 test.wav
aplay test.wav

# Check Vosk model path
ls vosk-model-small-en-us-0.15/

# Test Vosk directly
python3 -c "
import vosk
m = vosk.Model('vosk-model-small-en-us-0.15')
print('Vosk model loaded OK')
"
```

### "LoRa module not responding"
```bash
# Check UART device
ls /dev/ttyS*
# Or USB-UART adapter:
ls /dev/ttyUSB*

# Test AT commands manually
screen /dev/ttyS0 115200
# Type: AT
# Should respond: +OK

# Check RYLR998 power (3.3V required — NOT 5V)
# Measure with multimeter at VDD pin

# Check TX/RX not swapped (common mistake)
# RYLR998 TX → Jetson RX, RYLR998 RX → Jetson TX
```

### "Face recognition very slow"
```bash
# Install dlib with CUDA support for Jetson
pip uninstall dlib face-recognition
pip install cmake
git clone https://github.com/davisking/dlib.git
cd dlib
python3 setup.py install --set USE_AVX_INSTRUCTIONS=1 --set DLIB_USE_CUDA=1

# Reduce face ID frame skip
# In config.py, FACE_ID_FRAME_SKIP = 10 (or higher) to reduce CPU load
```

### "Battery failsafe triggering too early"
```bash
# Check battery voltage reading in QGC
# If voltage reads low, calibrate power module in Mission Planner:
# Battery Monitor → Measure battery voltage → enter actual measured voltage
# Verify BATTERY_CRITICAL_VOLTAGE in config.py matches your 6S minimum safe voltage
# 6S LiPo: 3.3V/cell × 6 = 19.8V (safe minimum for landing)
```

---

## Legal & Safety Warnings

### ⚠️ READ BEFORE OPERATING

**THIS SYSTEM INCLUDES AUTONOMOUS FLIGHT CAPABILITIES. Operator negligence can result in property damage, serious injury, or death.**

### Regulatory Requirements (USA — FAA)

- All drones over **0.55 lbs (250g)** must be **FAA-registered** (far6.faa.gov) — $5 fee
- Operations beyond visual line of sight (BVLOS) **require FAA waiver**
- Do not fly above **400 feet AGL** in uncontrolled airspace
- Do not fly within **5 miles of airports** without coordination
- Do not fly over people, moving vehicles, or stadiums
- Part 107 Remote Pilot Certificate required for commercial operations
- Always comply with **local ordinances** — some cities prohibit drone flight entirely

### ⚠️ Thermal Camera — Privacy Laws
FLIR thermal cameras can detect humans through some materials. **Operating thermal cameras over private property without consent may violate federal and state wiretapping/surveillance laws.** Consult an attorney before operating near populated areas.

### ⚠️ Face Recognition — Legal Restrictions
Real-time face recognition in public spaces is **restricted or illegal in multiple US cities and states** (Portland, OR; San Francisco, CA; Boston, MA; Illinois BIPA). Check your local laws before using the face recognition module.

### ⚠️ LoRa Radio — FCC Part 15/90
915MHz LoRa operation in the USA is permitted under FCC Part 15 at ≤ 1W ERP. **The RYLR998 at 22dBm (158mW) is legal.** Do not modify the RF output stage. Jamming or interfering with licensed communications is a federal felony.

### ⚠️ WiFi AP Mode
Creating an unauthorized WiFi access point may violate local laws in some jurisdictions. **AURA's WiFi relay is intended for emergency communications only.**

### Physical Safety Rules
1. **Never fly over people** — treat every flight as if a motor could fail at any moment
2. **Always have physical control** — keep RC transmitter on hand with KILL SWITCH programmed
3. **Never fly near power lines** — electromagnetic interference causes MAVLink instability
4. **Keep clear of the propeller arc** — minimum 3 meters at all times when armed
5. **Battery safety** — use a LiPo-safe bag for storage and charging, never leave charging unattended
6. **Never arm indoors** unless props removed
7. **Test all autonomous modes in open field first** — never test near structures or people
8. **Pre-arm check must pass** — never override ArduPilot pre-arm warnings manually

---

## Roadmap

### Version 1.1 (Next)
- [ ] Optical flow sensor integration for GPS-denied indoor flight
- [ ] SLAM (Simultaneous Localization and Mapping) using depth camera
- [ ] Multi-drone swarm coordination via LoRa mesh
- [ ] Mesh networking: multiple AURA drones extend relay range

### Version 1.2
- [ ] Precision landing on AprilTag marker
- [ ] Automated recharging pad docking
- [ ] Night vision via low-light camera upgrade (IMX462)
- [ ] Satellite messenger integration (Garmin inReach via UART)

### Version 2.0
- [ ] Fixed-wing or VTOL conversion option for 3× range
- [ ] Solar trickle charging for extended deployment
- [ ] Sub-GHz LoRa mesh to Meshtastic ground nodes
- [ ] Encrypted video stream to ground station via WiFi 802.11s mesh

### AI Improvements
- [ ] Fine-tuned YOLO model on survival-relevant classes (military vehicles, camps, traps)
- [ ] Behavioral analysis: distinguish walking vs running vs fighting from above
- [ ] Automated path planning around detected threats
- [ ] Multi-agent coordination: AURA talks to other AURA instances

---

*AURA Drone — Built for when the grid goes down and you need eyes in the sky.*

*Stay safe. Stay aware. Stay alive.*
