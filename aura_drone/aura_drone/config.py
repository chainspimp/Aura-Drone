"""
config.py — AURA Drone System Configuration

All system-wide constants and configuration values live here.
Sensitive values (encryption keys, passwords) are loaded from .env file.
Never hardcode credentials or paths in individual modules.
"""

import os
from dotenv import load_dotenv

# Load .env file for sensitive values
load_dotenv()

# ─────────────────────────────────────────────
# MAVLink / Flight Controller
# ─────────────────────────────────────────────
MAVLINK_PORT: str = os.getenv("MAVLINK_PORT", "/dev/ttyUSB0")
MAVLINK_BAUD: int = int(os.getenv("MAVLINK_BAUD", "921600"))
MAVLINK_TIMEOUT_S: int = 30          # Seconds to wait for heartbeat on connect
GUIDED_MODE_ARRIVAL_RADIUS_M: float = 1.5   # Distance (m) to consider waypoint reached
TAKEOFF_CLIMB_RATE_MS: float = 2.0   # m/s climb rate during takeoff
ORBIT_STEP_DEG: float = 5.0          # Degrees per orbit step (smaller = smoother circle)

# ─────────────────────────────────────────────
# Cameras
# ─────────────────────────────────────────────
MAIN_CAMERA_ID: int = int(os.getenv("MAIN_CAMERA_ID", "0"))       # ArduCam IMX477 HQ (USB)
DOWNWARD_CAMERA_ID: int = int(os.getenv("DOWNWARD_CAMERA_ID", "1"))  # OV5647 (CSI → v4l2)
THERMAL_DEVICE: str = os.getenv("THERMAL_DEVICE", "PureThermal")  # FLIR Lepton via PureThermal2
THERMAL_CAMERA_ID: int = int(os.getenv("THERMAL_CAMERA_ID", "2")) # PureThermal USB v4l2 id
MAIN_CAM_WIDTH: int = 1280
MAIN_CAM_HEIGHT: int = 720
MAIN_CAM_FPS: int = 30
YOLO_INFERENCE_SIZE: int = 640        # Input resolution for YOLO (square)
FACE_ID_FRAME_SKIP: int = 5          # Process every Nth frame for face recognition (CPU budget)

# ─────────────────────────────────────────────
# LoRa Radio (RYLR998 @ 915 MHz)
# ─────────────────────────────────────────────
LORA_PORT: str = os.getenv("LORA_PORT", "/dev/ttyS0")
LORA_BAUD: int = int(os.getenv("LORA_BAUD", "115200"))
LORA_NETWORK_ID: int = 18            # Must match all units on the network
LORA_BAND: int = 915000000           # 915 MHz (US)
LORA_SPREADING_FACTOR: int = 9
LORA_BANDWIDTH: int = 7              # 125 kHz
LORA_CODING_RATE: int = 1
LORA_POWER_DBM: int = 22             # Max TX power (check local regulations)
LORA_MY_ADDRESS: int = 1             # This drone's address on the LoRa network
LORA_ENCRYPTION_KEY: str = os.getenv("LORA_ENCRYPTION_KEY", "")   # 16-byte AES key (hex)
LORA_RETRY_INTERVAL_S: int = 60      # Seconds between retries to unreachable units

# ─────────────────────────────────────────────
# WiFi Relay
# ─────────────────────────────────────────────
WIFI_INTERFACE: str = os.getenv("WIFI_INTERFACE", "wlan1")         # Alfa adapter interface
WIFI_SSID: str = os.getenv("WIFI_SSID", "AURA-RELAY")
WIFI_PASSWORD: str = os.getenv("WIFI_PASSWORD", "survival2024!")
WIFI_CHANNEL: int = 6
WIFI_COUNTRY_CODE: str = "US"

# ─────────────────────────────────────────────
# Voice / Audio
# ─────────────────────────────────────────────
VOSK_MODEL_PATH: str = os.getenv("VOSK_MODEL_PATH", "vosk-model-small-en-us-0.15")
PIPER_PATH: str = os.getenv("PIPER_PATH", "/usr/local/bin/piper")
PIPER_MODEL: str = os.getenv("PIPER_MODEL", "en_US-hfc_female-medium.onnx")
AUDIO_SAMPLE_RATE: int = 16000
AUDIO_BLOCK_SIZE: int = 4000
WAKE_WORD: str = "hey aura"
WAKE_WORD_SENSITIVITY: float = 0.7   # 0.0-1.0, higher = fewer false positives

# ─────────────────────────────────────────────
# AI / Ollama
# ─────────────────────────────────────────────
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_CHAT_URL: str = os.getenv("OLLAMA_CHAT_URL", "http://localhost:11434/api/chat")
OLLAMA_MAIN_MODEL: str = "gemma3n:e2b"          # Fast model for command parsing + summaries
OLLAMA_REASONING_MODEL: str = "deepseek-r1:8b"  # Slow/deep model for mission planning
OLLAMA_VISION_MODEL: str = "qwen2.5-vl:7b"      # Multimodal model for scene description
OLLAMA_TIMEOUT_S: int = 120          # Max wait for Ollama response
OLLAMA_CACHE_MAX_ENTRIES: int = 100  # LRU cache size for repeated queries

# ─────────────────────────────────────────────
# YOLO Detection
# ─────────────────────────────────────────────
YOLO_MODEL_PATH: str = os.getenv("YOLO_MODEL_PATH", "yolov8n.pt")  # Or .engine for TensorRT
YOLO_CONFIDENCE_THRESHOLD: float = 0.45
YOLO_IOU_THRESHOLD: float = 0.45
# Classes we care about for threat/resource detection
YOLO_ALERT_CLASSES: list = [
    "person", "car", "truck", "motorcycle", "bicycle",
    "boat", "dog", "cat", "backpack", "suitcase"
]
YOLO_THREAT_CLASSES: list = ["person", "car", "truck", "motorcycle"]
YOLO_RESOURCE_CLASSES: list = ["boat", "bicycle", "backpack", "suitcase"]

# ─────────────────────────────────────────────
# Thermal Detection
# ─────────────────────────────────────────────
HUMAN_TEMP_MIN_C: float = 34.0       # Minimum skin temp to flag as human
HUMAN_TEMP_MAX_C: float = 39.0       # Maximum (fever threshold)
THERMAL_BLOB_MIN_PIXELS: int = 20    # Min blob size to avoid noise alerts (pixels)
THERMAL_LEPTON_WIDTH: int = 160      # Lepton 3.5 native resolution
THERMAL_LEPTON_HEIGHT: int = 120
THERMAL_ALERT_COOLDOWN_S: float = 5.0  # Seconds between repeated alerts for same blob

# ─────────────────────────────────────────────
# Flight Operational Parameters
# ─────────────────────────────────────────────
PATROL_ALTITUDE_M: float = float(os.getenv("PATROL_ALTITUDE_M", "30"))
SCOUT_ALTITUDE_M: float = float(os.getenv("SCOUT_ALTITUDE_M", "50"))
RELAY_ALTITUDE_M: float = float(os.getenv("RELAY_ALTITUDE_M", "60"))
MAX_ALTITUDE_M: float = 120.0        # Hard ceiling (legal limit in most jurisdictions)
DEFAULT_AIRSPEED_MS: float = 8.0     # m/s cruise speed
MAX_AIRSPEED_MS: float = 15.0
SCOUT_PHOTO_INTERVAL_M: float = 10.0 # Capture image every N meters during scout
PATROL_WAYPOINT_DWELL_S: float = 3.0 # Seconds to hover at each patrol waypoint
ORBIT_DEFAULT_RADIUS_M: float = 20.0
ORBIT_DEFAULT_SPEED_MS: float = 3.0

# ─────────────────────────────────────────────
# Battery Management
# ─────────────────────────────────────────────
BATTERY_CELLS: int = 6               # 6S LiPo
BATTERY_WARN_PERCENT: int = int(os.getenv("BATTERY_WARN_PERCENT", "30"))
BATTERY_CRITICAL_PERCENT: int = int(os.getenv("BATTERY_CRITICAL_PERCENT", "15"))
BATTERY_CRITICAL_VOLTAGE: float = 19.8   # 6S LiPo (3.3V/cell)
BATTERY_WARN_VOLTAGE: float = 21.6       # 6S (3.6V/cell)
BATTERY_MONITOR_INTERVAL_S: float = 5.0

# ─────────────────────────────────────────────
# Gimbal
# ─────────────────────────────────────────────
GIMBAL_TILT_NADIR: int = -90         # Degrees: straight down for mapping
GIMBAL_TILT_FORWARD: int = 0         # Degrees: forward-looking
GIMBAL_TILT_DEFAULT: int = -45       # Degrees: 45° down for patrol

# ─────────────────────────────────────────────
# Payload / Servo
# ─────────────────────────────────────────────
PAYLOAD_SERVO_OPEN_PWM: int = 2000   # PWM microseconds for release position
PAYLOAD_SERVO_CLOSED_PWM: int = 1000 # PWM microseconds for hold position
PAYLOAD_RELEASE_DWELL_S: float = 2.0 # Seconds to hold servo open

# ─────────────────────────────────────────────
# Paths / Storage
# ─────────────────────────────────────────────
KNOWN_FACES_DIR: str = os.getenv("KNOWN_FACES_DIR", "known_faces/")
DETECTION_LOG_DIR: str = os.getenv("DETECTION_LOG_DIR", "detection_logs/")
SCOUT_REPORT_DIR: str = os.getenv("SCOUT_REPORT_DIR", "scout_reports/")
MAP_OUTPUT_DIR: str = os.getenv("MAP_OUTPUT_DIR", "maps/")
LOG_FILE: str = os.getenv("LOG_FILE", "logs/aura_drone.log")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ─────────────────────────────────────────────
# Face Recognition
# ─────────────────────────────────────────────
FACE_RECOGNITION_TOLERANCE: float = 0.5   # Lower = stricter matching
FACE_UNKNOWN_ALERT_COOLDOWN_S: float = 30.0  # Don't re-alert same unknown face for N sec

# ─────────────────────────────────────────────
# Ground Station UI
# ─────────────────────────────────────────────
UI_FEED_WIDTH: int = 640
UI_FEED_HEIGHT: int = 480
UI_UPDATE_INTERVAL_MS: int = 100     # Tkinter update interval
UI_MAX_ALERT_LOG_ENTRIES: int = 200  # Max lines in scrolling alert log
UI_MAP_GRID_SIZE: int = 400          # Pixels for the mini-map widget

# ─────────────────────────────────────────────
# Map Builder
# ─────────────────────────────────────────────
MAP_STITCH_OVERLAP: float = 0.3      # 30% overlap expected between adjacent photos
MAP_DEFAULT_ZOOM: int = 17           # Default zoom level for folium maps
