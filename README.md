# AURA Drone — Autonomous Unified Reconnaissance Assistant
### Version 2.0

> **A fully autonomous AI survival drone. No internet. No cloud. No subscription. No infrastructure. Just you, the sky, and AURA.**

AURA is a Python-based autonomous drone system built around a **Pixhawk 6C Mini** flight controller and an **NVIDIA Jetson Orin Nano** companion computer. All AI inference runs 100% locally via Ollama. Designed for SHTF, off-grid security, disaster response, and austere-environment reconnaissance.

---

## Table of Contents

1. [What AURA Can Do](#what-aura-can-do)
2. [How the Ground Station Works](#how-the-ground-station-works)
3. [Full Parts List](#full-parts-list)
4. [Total Cost](#total-cost)
5. [Project File Structure](#project-file-structure)
6. [Software Installation — Drone (Jetson)](#software-installation--drone-jetson)
7. [Software Installation — Laptop](#software-installation--laptop)
8. [Wiring Diagram](#wiring-diagram)
9. [ArduPilot Parameters](#ardupilot-parameters)
10. [First Startup Guide](#first-startup-guide)
11. [How to Fly — Step by Step](#how-to-fly--step-by-step)
12. [Ground Station Modes](#ground-station-modes)
13. [Voice Command Reference](#voice-command-reference)
14. [Troubleshooting](#troubleshooting)
15. [Legal & Safety Warnings](#legal--safety-warnings)
16. [License](#license)
17. [Roadmap](#roadmap)

---

## What AURA Can Do

### 👁️ See Threats Before They Reach You
- **YOLOv8 real-time detection** — identifies people, vehicles, boats, and gear at up to 60fps using TensorRT on the Jetson
- **FLIR Lepton 3.5 thermal camera** — detects human body heat day or night, through smoke, darkness, and light camouflage
- **Face recognition** — knows your group by face, alerts on strangers with a cropped photo saved to disk
- **Threat scoring** — rates every detection NONE / LOW / MEDIUM / HIGH / CRITICAL with a plain-English operator recommendation
- **Perimeter patrol** — flies a continuous GPS loop around your camp, scanning at every waypoint, 24/7

### 🗺️ Scout So You Never Walk Blind
- **Corridor scout** — flies a systematic S-pattern scan of any road, trail, or area and returns a full AI-written threat report
- **Building assessment** — orbits a structure at multiple altitudes, tells you if it's occupied or safe to enter
- **Resource finder** — detects water sources, crops, and supply caches from the air using color analysis and object detection
- **Situation map** — generates an interactive HTML map with your flight path and every geo-tagged detection pinned on it

### 📡 Communicate When Everything Else is Dead
- **LoRa 915MHz long-range link** — AES-128 encrypted telemetry, alerts, and commands between laptop and drone at **15–20 miles range**
- **WiFi hotspot relay** — broadcasts a WPA2 hotspot from 60m altitude, extends ground team comms up to **~1 mile**, enables full camera GUI
- **Mode switching** — one button in the LoRa terminal commands the drone to switch to WiFi relay mode so you can pull up the full video GUI
- **Physical message courier** — when radio range isn't enough, flies to another camp, delivers a message via WiFi, and returns
- **Bluetooth local link** — short-range direct laptop connection for pre-flight and nearby operations (~300ft)

### 🧠 Local AI — Zero Internet Required
- **Natural language commands** — say or type anything in plain English, AURA figures out what you mean via `gemma3n:e2b`
- **Mission planning** — describe a goal in plain English, AURA plans the steps using `deepseek-r1:8b`
- **Scene description** — ask "what do you see?" and get a tactical description from `qwen2.5-vl:7b`
- **Situation reports** — one command generates a full SITREP from all active sensors, forwarded to your laptop over LoRa
- **Graceful degradation** — if the AI goes down, rule-based logic keeps everything flying and responding

### 🎙️ Hands-Free Voice Control
- **Wake word** — say "Hey AURA" to activate
- **Vosk offline speech recognition** — no internet, runs entirely on the Jetson
- **Piper TTS** — natural-sounding spoken alerts through the onboard speaker

### 🔒 Safety First, Always
- **Battery monitor** — independent thread that forces RTH or landing no matter what else is happening
- **GPS loss handler** — auto-LOITER then forced landing if GPS doesn't recover in 30 seconds
- **Comms loss watchdog** — auto-RTH after 30 seconds of lost ground station link
- **Payload zone check** — scans directly below for people before releasing payload

---

## How the Ground Station Works

AURA supports **three connection modes**. You can switch between them mid-mission.

```
┌─────────────────────────────────────────────────────────────────┐
│                    GROUND STATION MODES                         │
├─────────────────┬───────────────────────────────────────────────┤
│ MODE            │ DETAILS                                        │
├─────────────────┼───────────────────────────────────────────────┤
│ LoRa            │ Range: 15-20 miles line of sight               │
│ Long Range      │ Data: text telemetry, alerts, detections,      │
│                 │       full commands, situation reports          │
│                 │ Video: none (bandwidth too low)                 │
│                 │ Run: python lora_client.py on your laptop       │
│                 │ Hardware: RYLR998 + CP2102 USB dongle (~$33)    │
├─────────────────┼───────────────────────────────────────────────┤
│ WiFi Relay      │ Range: ~1 mile                                  │
│ Full GUI        │ Data: everything — live video, thermal,         │
│                 │       full telemetry, all alerts                │
│                 │ Video: live YOLO camera + false-color thermal   │
│                 │ Connect laptop to "AURA-RELAY" WiFi             │
│                 │ Run: python ground_station.py                   │
├─────────────────┼───────────────────────────────────────────────┤
│ Bluetooth       │ Range: ~300ft built-in / ~600ft Class 1 adapter │
│ Local           │ Data: telemetry, alerts, detections, commands   │
│                 │ Best for: pre-flight, nearby base camp ops       │
│                 │ Run: python bt_client.py on your laptop          │
└─────────────────┴───────────────────────────────────────────────┘
```

### How to Switch Modes Mid-Mission

**LoRa → WiFi (when you want to see the camera):**
1. In `lora_client.py` click **"Switch to WiFi"** button
2. Drone receives command, climbs to 60m, starts `AURA-RELAY` hotspot
3. Connect your laptop WiFi to `AURA-RELAY`
4. Close `lora_client.py`, run `ground_station.py`

**WiFi → LoRa (drone flying far out):**
- Happens automatically when drone flies out of WiFi range
- Open `lora_client.py` — picks up the LoRa stream immediately

---

## Full Parts List

### 🪁 Frame & Motors

| Component | Model | Price | Where to Buy |
|-----------|-------|-------|--------------|
| Frame | Tarot 650 Iron Man (carbon fiber, foldable) | $75 | AliExpress, Amazon |
| Motors × 4 | T-Motor MN3110-17 KV700 | $35 each | T-Motor Direct |
| ESCs × 4 | BLHeli32 45A 3-6S | $18 each | Amazon, Banggood |
| Propellers × 4 pairs | 15×5.0 Carbon Fiber CW/CCW | $12/pair | Amazon |

### ⚡ Power System

| Component | Model | Price | Where to Buy |
|-----------|-------|-------|--------------|
| Battery × 2 | Tattu 6S 10000mAh 25C | $110 each | Tattu Direct, GetFPV |
| Power Distribution | Matek FCHUB-6S | $22 | Matek, Amazon |
| Power Module | Holybro PM07 (90A, voltage sensing) | $25 | Holybro Direct |
| XT60 Connectors × 4 | Genuine XT60 | $8/10pk | Amazon |

### 🎮 Flight Controller

| Component | Model | Price | Where to Buy |
|-----------|-------|-------|--------------|
| **Flight Controller** | **Pixhawk 6C Mini** | $180 | Holybro Direct |
| **GPS** | **Holybro M9N** (u-blox M9N, 25Hz) | $65 | Holybro Direct |
| GPS Mast | 30cm carbon mast | $8 | Amazon |

### 🖥️ Companion Computer

| Component | Model | Price | Where to Buy |
|-----------|-------|-------|--------------|
| **Companion Computer** | **NVIDIA Jetson Orin Nano 8GB** | $499 | NVIDIA, Arrow Electronics |
| Storage | Samsung Pro Endurance 256GB microSD | $35 | Amazon |
| SSD (optional) | WD SN550 500GB M.2 NVMe | $50 | Amazon |
| Cooling | Jetson Orin Nano passive heatsink | $15 | Amazon |

### 📷 Cameras & Sensors

| Component | Model | Price | Where to Buy |
|-----------|-------|-------|--------------|
| Main Camera | ArduCam IMX477 HQ USB (12.3MP) | $65 | ArduCam Direct |
| HQ Lens | 6mm CS-Mount (63° FoV) | $25 | ArduCam, Amazon |
| **Thermal Camera** | **FLIR Lepton 3.5** (160×120 radiometric) | $200 | FLIR, DigiKey |
| **Thermal Board** | **GetThermal PureThermal 2** (USB UVC) | $99 | GroupGets.com |
| Downward Camera | ArduCam OV5647 CSI | $20 | ArduCam, Amazon |
| Rangefinder | TFmini-S LiDAR (12m, 100Hz) | $35 | Amazon |

### 📻 Communications

| Component | Model | Price | Where to Buy |
|-----------|-------|-------|--------------|
| WiFi Adapter | **Alfa AWUS036ACM** (AP mode, AC1200) | $45 | Amazon |
| WiFi Antenna | Alfa ARS-N19 9dBi | $18 | Amazon |
| **LoRa Radio × 2** | **RYLR998 915MHz** (one drone, one laptop) | $28 each | Amazon |
| LoRa Antenna × 2 | SMA 915MHz 3dBi Whip | $8 each | Amazon |
| **USB-UART Adapter** | **CP2102 module** (for laptop LoRa dongle) | $5 | Amazon |

> You need **two** RYLR998 modules — one mounts on the drone, one connects to your laptop via the CP2102 adapter. This is your long-range link. Total laptop dongle cost: ~$33.

### 🔊 Audio

| Component | Model | Price | Where to Buy |
|-----------|-------|-------|--------------|
| I2S Amplifier | MAX98357A breakout (3W, I2S) | $6 | SparkFun, Amazon |
| Speaker | Visaton FRS 5 (3W, 8Ω, 50mm) | $8 | Amazon |
| Microphone | SPH0645 I2S MEMS | $8 | Adafruit |

### 🦾 Gimbal & Payload

| Component | Model | Price | Where to Buy |
|-----------|-------|-------|--------------|
| Gimbal | **Tarot T2-2D** (2-axis brushless, PWM) | $85 | Amazon, AliExpress |
| Payload Servo | Tower Pro SG90 (9g micro servo) | $5 | Amazon |

### 🔌 Wiring & Misc

| Item | Price |
|------|-------|
| JST-GH 6-pin cables (Pixhawk telemetry) | $10 |
| M3 vibration damping standoffs | $6 |
| Heat shrink assortment | $8 |
| Nylon cable ties 150mm | $5 |
| Kapton tape | $6 |
| Jumper wires (for LoRa wiring) | $5 |

---

## Total Cost

| Build | Description | Est. Total |
|-------|-------------|-----------|
| **Full build** | Everything above, 2 batteries | **~$1,850** |
| **Without thermal** | Skip FLIR Lepton + PureThermal | **~$1,550** |
| **Budget build** | RPi 4 instead of Jetson, no thermal | **~$900** |
| **Laptop LoRa dongle** | RYLR998 + CP2102 (add to any laptop) | **~$33** |

> The Jetson Orin Nano ($499) is the biggest single cost. It is required for real-time YOLOv8, thermal processing, Ollama AI, and voice recognition running simultaneously. A Raspberry Pi 4 can substitute but YOLO will drop to ~5fps and Ollama will be very slow.

---

## Project File Structure

```
aura_drone/
│
├── drone_main.py              ← START HERE — boots the entire system
├── config.py                  ← All settings (ports, altitudes, thresholds)
├── requirements.txt           ← Python dependencies
├── .env.example               ← Copy to .env and fill in your values
├── LICENSE                    ← Non-commercial open source license
│
├── lora_client.py             ← LAPTOP: Long-range LoRa ground terminal
├── bt_client.py               ← LAPTOP: Bluetooth ground station client
│
├── flight/
│   ├── drone_control.py       ← MAVLink bridge (takeoff, land, fly, orbit)
│   ├── perimeter_patrol.py    ← Autonomous GPS waypoint patrol loop
│   ├── route_scout.py         ← S-pattern corridor scan + AI report
│   ├── payload_release.py     ← Safe servo-controlled payload drop
│   └── emergency.py           ← Battery, GPS, and comms failsafes
│
├── vision/
│   ├── yolo_watch.py          ← YOLOv8 real-time object detection thread
│   ├── thermal_watch.py       ← FLIR Lepton heat blob detection
│   ├── face_id.py             ← Known vs unknown face recognition
│   ├── building_scan.py       ← Multi-altitude structure assessment
│   └── resource_finder.py     ← Water, crops, supply cache detection
│
├── ai/
│   ├── mission_planner.py     ← DeepSeek-R1 mission planning + reasoning
│   ├── command_parser.py      ← Natural language to drone action parser
│   ├── situation_report.py    ← Multi-sensor SITREP generator
│   └── threat_assessor.py     ← Real-time threat classification
│
├── comms/
│   ├── lora_bridge.py         ← RYLR998 serial driver + AES encryption
│   ├── lora_telemetry.py      ← Pushes live telemetry/alerts over LoRa
│   ├── wifi_relay.py          ← Creates AURA-RELAY WiFi hotspot
│   ├── bluetooth_bridge.py    ← Bluetooth RFCOMM server
│   ├── message_courier.py     ← Physical flight message delivery
│   └── map_builder.py         ← Folium interactive HTML map builder
│
├── voice/
│   ├── speech_input.py        ← Vosk offline speech recognition
│   ├── tts_output.py          ← Piper TTS with priority queue
│   └── wake_listener.py       ← "Hey AURA" wake word detection
│
├── ui/
│   ├── ground_station.py      ← Full Tkinter GUI (WiFi/local use)
│   └── alert_manager.py       ← Central alert bus and routing
│
├── known_faces/               ← Add .jpg photos of your group here
├── detection_logs/            ← Auto-created on first run
├── scout_reports/             ← Auto-created on first run
└── logs/                      ← Auto-created, aura_drone.log lives here
```

---

## Software Installation — Drone (Jetson)

### Step 1 — Flash JetPack

Download **NVIDIA SDK Manager** from developer.nvidia.com/sdk-manager. Flash **JetPack 5.1.3** (Ubuntu 22.04 + CUDA + TensorRT) to your Jetson Orin Nano following NVIDIA's official guide.

### Step 2 — System Packages

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y \
    python3-pip python3-tk python3-dev git curl wget \
    portaudio19-dev espeak alsa-utils \
    hostapd dnsmasq \
    libatlas-base-dev libopenblas-dev \
    cmake build-essential libboost-all-dev \
    bluetooth bluez python3-bluetooth \
    v4l-utils

# Max performance mode
sudo nvpmodel -m 0
sudo jetson_clocks
```

### Step 3 — Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable ollama
sudo systemctl start ollama

# Pull all three AI models (30-60 min on first run)
ollama pull gemma3n:e2b          # ~1.5GB  fast commands
ollama pull deepseek-r1:8b       # ~4.7GB  mission planning
ollama pull qwen2.5-vl:7b        # ~4.4GB  visual descriptions

ollama list  # verify all three appear
```

### Step 4 — Clone and Install AURA

```bash
git clone https://github.com/YOUR_USERNAME/aura-drone.git
cd aura-drone

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

# FLIR Lepton driver (install from source for Jetson)
git clone https://github.com/groupgets/pylepton.git
cd pylepton && pip install . && cd ..
```

### Step 5 — Configure Your .env File

```bash
cp .env.example .env
nano .env
```

Minimum required values:

```env
MAVLINK_PORT=/dev/ttyUSB0
LORA_PORT=/dev/ttyS0
LORA_ENCRYPTION_KEY=              # Generate below
WIFI_PASSWORD=your_strong_password
```

Generate encryption key (do this once, save it — you need it on your laptop too):

```bash
python3 -c "import secrets; print(secrets.token_hex(16))"
# Paste the output into LORA_ENCRYPTION_KEY in .env
```

### Step 6 — Download Vosk Speech Model

```bash
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
# Folder vosk-model-small-en-us-0.15/ now exists in project root
```

### Step 7 — Install Piper TTS

```bash
wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_aarch64.tar.gz
tar -xzf piper_linux_aarch64.tar.gz
sudo mv piper/piper /usr/local/bin/
sudo chmod +x /usr/local/bin/piper

wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_female/medium/en_US-hfc_female-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_female/medium/en_US-hfc_female-medium.onnx.json
```

### Step 8 — TensorRT YOLO Acceleration (Recommended)

Speeds up YOLO detection 5x on Jetson. Run once, takes about 10 minutes.

```bash
python3 -c "
from ultralytics import YOLO
YOLO('yolov8n.pt').export(format='engine', device=0, half=True, imgsz=640)
print('Done — yolov8n.engine created')
"
```

### Step 9 — Add Your Group's Faces

```bash
# One clear front-facing photo per person
# Filename = person's name with underscores
cp /path/to/photo.jpg known_faces/Alice_Smith.jpg
cp /path/to/photo.jpg known_faces/Bob_Jones.jpg

# Multiple photos = better accuracy
# Alice_Smith_1.jpg, Alice_Smith_2.jpg, etc.
```

### Step 10 — Start AURA

```bash
source venv/bin/activate

# Full GUI on Jetson screen
python3 drone_main.py

# Headless — SSH session, no screen needed
python3 drone_main.py --no-gui

# Test all systems without hardware
python3 drone_main.py --sim
```

You should see a startup health check like this:

```
  alerts               ✓ OK
  tts                  ✓ OK
  mavlink              ✓ OK
  yolo                 ✓ OK
  thermal              ✓ OK
  face_id              ✓ OK
  ai                   ✓ OK
  lora                 ✓ OK
  bluetooth            ✓ OK
  voice                ✓ OK
AURA online. All systems nominal. Awaiting commands.
```

Any failed subsystem shows ✗ FAILED — AURA keeps running without it. The only hard requirements are MAVLink and at least one camera.

---

## Software Installation — Laptop

### Install Python Client Software

```bash
# Windows / Mac / Linux
pip install pyserial pycryptodome python-dotenv

# For Bluetooth client
pip install PyBluez

# Linux also needs
sudo apt install bluetooth bluez python3-bluetooth
```

### Wire Your Laptop LoRa Dongle

This is what gives you the 15-20 mile range from your laptop.

```
RYLR998 module ──► CP2102 USB-UART adapter ──► Laptop USB port

RYLR998 VDD  ──── CP2102 3.3V
RYLR998 GND  ──── CP2102 GND
RYLR998 TX   ──── CP2102 RX
RYLR998 RX   ──── CP2102 TX
RYLR998 ANT  ──── 915MHz SMA antenna  ← attach this before powering on
```

Total cost: ~$33. Plugs into any laptop. No drivers needed on Windows 10+.

### Set Your Encryption Key on the Laptop

Create a `.env` file in the same folder as `lora_client.py`:

```env
LORA_ENCRYPTION_KEY=<same key you generated on the drone>
```

Both modules must use the exact same key or messages won't decrypt.

### Run the LoRa Ground Terminal

```bash
python lora_client.py                         # auto-detects COM port
python lora_client.py --port COM3             # Windows — specify port
python lora_client.py --port /dev/ttyUSB0     # Linux
```

### Run the Bluetooth Client

```bash
# First time — pair with drone (Linux)
bluetoothctl
> scan on
> pair AA:BB:CC:DD:EE:FF    # drone's Bluetooth MAC
> trust AA:BB:CC:DD:EE:FF
> exit

# Connect
python bt_client.py              # auto-scans and connects
python bt_client.py --cli        # no GUI, terminal only
```

---

## Wiring Diagram

```
6S LiPo Battery (22.2V)
│
├── XT60 ──► Matek FCHUB-6S Power Distribution
│             ├── 4× BLHeli32 ESC ──► 4× T-Motor MN3110
│             ├── 12V BEC ──────────► Jetson Orin Nano (12V barrel)
│             └── 5V BEC ───────────► Holybro PM07 ──► Pixhawk POWER1

Pixhawk 6C Mini:
├── TELEM2 (JST-GH) ────────────────► CP2102 USB ──► Jetson USB  [MAVLink 921600]
├── GPS1 ────────────────────────────► Holybro M9N GPS
├── AUX OUT 1 ───────────────────────► Tarot T2-2D Gimbal (PWM)
├── AUX OUT 2 ───────────────────────► SG90 Payload Servo (PWM)
└── MOTOR OUT 1-4 ───────────────────► 4× ESC Signal

Jetson Orin Nano:
├── USB Port 1 ──────────────────────► ArduCam IMX477 HQ (main camera)
├── USB Port 2 ──────────────────────► PureThermal 2 + FLIR Lepton 3.5
├── USB Port 3 ──────────────────────► CP2102 ──► Pixhawk TELEM2
├── USB Port 4 ──────────────────────► Alfa AWUS036ACM WiFi adapter
├── CSI Port ────────────────────────► ArduCam OV5647 (downward camera)
│
├── UART ttyS0:
│     Pin 8  TX ─────────────────────► RYLR998 RX
│     Pin 10 RX ─────────────────────► RYLR998 TX
│     Pin 1  3.3V ───────────────────► RYLR998 VDD
│     Pin 6  GND ────────────────────► RYLR998 GND
│                                       RYLR998 ANT ──► 915MHz antenna
│
├── I2S Audio OUT:
│     BCK/LRCK/DIN ──────────────────► MAX98357A ──► Speaker
│
└── I2S Audio IN:
      SCK/WS/SD ──────────────────────► SPH0645 Microphone

Laptop LoRa Dongle:
  RYLR998 ──► CP2102 USB-UART ──► Laptop USB
  (see wiring above — same connections)
```

---

## ArduPilot Parameters

Connect via Mission Planner or QGroundControl over USB. Go to **Config → Full Parameter List**.

```
SERIAL2_BAUD        921600    TELEM2 baud (Jetson connection)
SERIAL2_PROTOCOL    2         MAVLink 2.0

FS_BATT_ENABLE      1         Battery failsafe on
FS_BATT_VOLTAGE     19.8      Force land at 3.3V/cell × 6
FS_GCS_ENABLE       1         GCS heartbeat failsafe
FS_GCS_TIMEOUT      5         Seconds before RTH

GUID_TIMEOUT        10        GUIDED mode timeout

MNT_TYPE            1         Servo gimbal (Tarot T2-2D)
SERVO9_FUNCTION     7         AUX1 = gimbal tilt
MNT_ANGMIN_TIL      -9000     -90 degrees (straight down)
MNT_ANGMAX_TIL      0         0 degrees (forward)

SERVO10_FUNCTION    0         AUX2 = payload servo (manual)
SERVO10_MIN         1000      Closed
SERVO10_MAX         2000      Open/release
SERVO10_TRIM        1000      Default closed

GPS_TYPE            1         Auto-detect u-blox
AHRS_EKF_TYPE       3         EKF3
EK3_ENABLE          1

RNGFND1_TYPE        20        TFmini-S LiDAR
RNGFND1_ORIENT      25        Downward facing
RNGFND1_MIN_CM      30
RNGFND1_MAX_CM      1200
```

---

## First Startup Guide

### Pre-Flight Hardware Checklist

- [ ] Props tight, correct rotation direction (CW/CCW per motor position)
- [ ] Battery fully charged (25.2V for 6S)
- [ ] Battery physically locked in frame, XT60 fully seated
- [ ] All cameras connected — check with `v4l2-ctl --list-devices`
- [ ] LoRa antenna screwed on before powering the RYLR998
- [ ] WiFi adapter visible — check with `ip link show wlan1`
- [ ] GPS has clear sky view, not blocked by carbon fiber
- [ ] Laptop LoRa dongle connected and antenna attached

### Pre-Flight Software Checks

```bash
# 1. Ollama is running
ollama list

# 2. Cameras visible
v4l2-ctl --list-devices

# 3. MAVLink connecting
python3 -c "
import sys; sys.path.insert(0,'.')
from flight.drone_control import DroneController
d = DroneController()
ok = d.connect('/dev/ttyUSB0', 921600)
print('MAVLink:', ok)
if ok: print(d.get_telemetry())
"

# 4. LoRa responding
python3 -c "
import serial, time
s = serial.Serial('/dev/ttyS0', 115200, timeout=2)
s.write(b'AT\r\n'); time.sleep(1)
print('LoRa:', s.readline())  # should print +OK
"

# 5. Clean startup test
python3 drone_main.py --sim --no-gui
# Watch for all ✓ OK then Ctrl-C
```

### On Your Laptop

```bash
python lora_client.py
# Should show: "LoRa module OK — waiting for drone messages..."
# Once drone_main.py is running on the Jetson you will see
# telemetry packets start appearing within 5 seconds
```

---

## How to Fly — Step by Step

**1. Power on drone** — connect battery, wait for Pixhawk startup tones

**2. Start AURA on Jetson**
```bash
cd aura-drone && source venv/bin/activate
python3 drone_main.py --no-gui
```

**3. Connect from laptop**
```bash
python lora_client.py
```
Watch telemetry start flowing — battery %, GPS fix, mode.

**4. Verify GPS lock** — wait for GPS FIX 3 and 8+ satellites in the telemetry panel before takeoff.

**5. Take off** — type in the command box or click the button:
```
takeoff 30
```
Or say: **"Hey AURA — takeoff"**

**6. Run a patrol** — type:
```
patrol
```

**7. Scout a route** — type:
```
scout
```
AURA flies an S-pattern, takes photos, runs YOLO, and sends you the AI-written report over LoRa.

**8. Get a situation report** — click **📋 SITREP** or type `sitrep`

**9. Switch to full camera view** — click **"Switch to WiFi"** in `lora_client.py`, wait ~30s, connect laptop to `AURA-RELAY` WiFi, then run:
```bash
python ground_station.py
```

**10. Return home** — type `rth` or click **⟲ RTH**

**11. Emergency land** — type `land` or hit your RC kill switch

---

## Ground Station Modes

### LoRa Terminal — `lora_client.py` (15-20 miles)

What you see when the drone is far away:
- **Telemetry panel** — battery bar, voltage, altitude, GPS coords, heading, speed, mode, armed status, flight timer, signal RSSI/SNR
- **Alert log** — every threat detection and system event, color coded (red=critical, yellow=warning, green=info)
- **Detection panel** — YOLO detections (red=person, orange=vehicle) and thermal contacts with GPS coordinates
- **Situation report panel** — full AI-written SITREP when you request one
- **Link watchdog** — LINK OK / LINK WEAK / LINK TIMEOUT indicator
- **Switch to WiFi button** — sends relay mode command to drone

### Full GUI — `ground_station.py` (~1 mile via WiFi)

What you see when connected to AURA-RELAY hotspot:
- **Live main camera feed** — 640×480 with YOLO bounding boxes (red=person, orange=vehicle, blue=gear)
- **Live thermal feed** — false-color FLIR image, alerts when human-temperature blobs detected
- **Telemetry panel** — same as LoRa terminal
- **Alert log** — scrolling color-coded threat log
- **Mini-map** — drone position, flight path trail, detection markers
- **Control bar** — all flight buttons plus text command input

### Bluetooth Client — `bt_client.py` (~300ft)

Same layout as LoRa terminal but connected via Bluetooth. Best for sitting at base camp when the drone is landed nearby, doing pre-flight checks, or reviewing downloaded scout reports.

---

## Voice Command Reference

Say **"Hey AURA"** first. AURA responds "Yes?" then listens for your command.

| Command | What Happens |
|---------|-------------|
| `Hey AURA` | Wake word — activates listening |
| `takeoff` | Arms and climbs to 30m |
| `takeoff 50` | Arms and climbs to 50m |
| `land` | Immediate descent and disarm |
| `return home` | RTL mode — flies home and lands |
| `hover` | LOITER — holds current position |
| `patrol` | Starts perimeter patrol loop |
| `stop patrol` | Holds position, ends patrol |
| `scout north` | Scouts ahead in current heading |
| `situation report` | Full SITREP from all sensors |
| `what do you see` | Vision AI describes current camera view |
| `drop payload` | Zone check then payload release |
| `relay mode` | Climbs to 60m and starts WiFi hotspot |
| `orbit` | Circles current position 20m radius |
| `set gimbal down` | Camera points straight down |
| `set gimbal forward` | Camera points forward |
| `send message [text]` | Broadcasts via LoRa to all units |

---

## Troubleshooting

### MAVLink connection failed
```bash
ls /dev/ttyUSB*                              # confirm device exists
sudo usermod -a -G dialout $USER && newgrp dialout  # fix permissions
screen /dev/ttyUSB0 921600                   # test raw serial (Ctrl-A K to exit)
```

### Camera not found
```bash
v4l2-ctl --list-devices                      # list all cameras
python3 -c "import cv2; print(cv2.VideoCapture(0).isOpened())"
# Try different IDs (0, 1, 2) if main camera isn't on 0
```

### Thermal not detected
```bash
lsusb | grep -i lepton                       # PureThermal USB device
dmesg | tail -20 | grep -i usb               # USB enumeration log
```

### Ollama not responding
```bash
sudo systemctl restart ollama
curl http://localhost:11434/api/tags          # should return JSON
ollama pull gemma3n:e2b                       # re-pull if missing
```

### LoRa module no response
```bash
# Test AT command manually
python3 -c "
import serial, time
s = serial.Serial('/dev/ttyS0', 115200, timeout=2)
s.write(b'AT\r\n'); time.sleep(1); print(s.readline())
"
# Should print: b'+OK\r\n'
# If nothing: check TX/RX swap (most common mistake)
# RYLR998 TX → Jetson RX  /  RYLR998 RX → Jetson TX
# Also: RYLR998 needs 3.3V not 5V
```

### Laptop LoRa gets no drone messages
```bash
# 1. Verify same LORA_ENCRYPTION_KEY in both .env files
# 2. Verify same LORA_NETWORK_ID (default: 18) in config.py
# 3. Check RSSI in lora_client.py — below -120 means too far or blocked
# 4. LoRa is line-of-sight — terrain and buildings cut range significantly
```

### Voice not working
```bash
arecord -D default -r 16000 -f S16_LE -d 3 /tmp/test.wav && aplay /tmp/test.wav
ls vosk-model-small-en-us-0.15/              # model must exist here
python3 -c "import vosk; vosk.Model('vosk-model-small-en-us-0.15'); print('OK')"
```

### YOLO running slowly
```bash
ls *.engine            # TensorRT engine should exist
# If missing, generate it:
python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt').export(format='engine', device=0, half=True)"
sudo nvpmodel -m 0 && sudo jetson_clocks     # max performance mode
```

---

## Legal & Safety Warnings

### ⚠️ READ BEFORE OPERATING

**This drone flies autonomously. Crashes cause property damage, serious injury, and death.**

### USA FAA Requirements
- Drones **over 0.55 lbs** must be **FAA registered** — far6.faa.gov — $5 fee
- Do not fly above **400 feet AGL** in uncontrolled airspace
- Do not fly **beyond visual line of sight** without FAA waiver
- Do not fly within **5 miles of airports** without coordination
- Do not fly **over people or moving vehicles**
- Part 107 required for any commercial operations

### Privacy Laws
- **Thermal cameras** may violate surveillance laws over private property — check your state
- **Face recognition** is restricted or illegal in multiple US cities and states — check local ordinances
- **LoRa radio** is legal under FCC Part 15 at the RYLR998's output power — do not modify hardware

### Physical Safety — Non-Negotiable
1. Never fly over people
2. Always have an RC transmitter with kill switch in your hand
3. Never arm with props on indoors
4. Keep 10 feet from propeller arc when armed
5. Use LiPo-safe bag for battery storage and charging
6. Never leave LiPo charging unattended
7. Do not fly in rain — electronics are not waterproofed
8. Test all autonomous modes in open field before using near structures

---

## License

AURA Drone uses the **AURA Drone Non-Commercial Public License**.

- ✅ Use, study, improve, and share your improvements
- ✅ Must keep same license and credit original project
- ❌ Cannot sell or use commercially without written permission
- ❌ Cannot strip author attribution

See `LICENSE` file. For commercial licensing contact the project maintainer via GitHub.

---

## Roadmap

### v2.1 — Next
- [ ] Optical flow sensor for GPS-denied indoor flight
- [ ] AprilTag precision landing on a marker pad
- [ ] Meshtastic integration — AURA talks to $30 T-Beam LoRa nodes carried by ground team members
- [ ] Multi-drone coordination — two AURA units share detections over LoRa mesh

### v2.2
- [ ] Automated recharging pad docking
- [ ] Night vision camera (IMX462 low-light)
- [ ] Satellite fallback (Garmin inReach via UART)
- [ ] Sub-GHz mesh to ground team handheld radios

### v3.0
- [ ] Fixed-wing or VTOL conversion for 3× range
- [ ] Solar trickle charging for multi-day deployment
- [ ] Custom-trained YOLO model on survival-relevant classes
- [ ] Encrypted video streaming over WiFi mesh between multiple AURA units

---

*AURA Drone — Built for when the grid goes down and you need eyes in the sky.*

*Stay safe. Stay aware. Stay alive.*
