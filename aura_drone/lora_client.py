"""
lora_client.py — AURA Long-Range LoRa Ground Terminal

Run this on your LAPTOP when the drone is far away (up to 15-20 miles).
Connects to a RYLR998 LoRa module plugged into your laptop via USB-UART.

You get:
  - Live telemetry (battery, altitude, GPS, speed, mode) updated every 5s
  - All alerts and threat detections as they happen
  - Full command control (takeoff, land, RTH, scout, patrol, etc.)
  - Automatic "come closer" command when you want to switch to WiFi/full GUI

Hardware needed on your laptop:
  - RYLR998 LoRa module (~$28)
  - CP2102 USB-UART adapter (~$5)
  - Wire them together (3.3V, GND, TX→RX, RX→TX)
  - Plug CP2102 into laptop USB

Usage:
  python lora_client.py                        # auto-detect COM port
  python lora_client.py --port COM3            # Windows
  python lora_client.py --port /dev/ttyUSB0   # Linux
  python lora_client.py --port /dev/tty.usbserial-0001  # Mac

Install:
  pip install pyserial pycryptodome windows-curses  # Windows
  pip install pyserial pycryptodome                 # Linux/Mac
"""

import argparse
import base64
import json
import os
import sys
import threading
import time
from datetime import datetime

import serial
import serial.tools.list_ports

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox, ttk
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

# ─────────────────────────────────────────────
# LoRa Config — must match drone's config.py
# ─────────────────────────────────────────────
LORA_BAUD          = 115200
LORA_NETWORK_ID    = 18
LORA_BAND          = 915000000
LORA_SF            = 9
LORA_BW            = 7
LORA_CR            = 1
LORA_POWER         = 22
LAPTOP_ADDRESS     = 2      # Drone is address 1, laptop is address 2
DRONE_ADDRESS      = 1
MAX_PAYLOAD        = 240
TELEMETRY_INTERVAL = 5      # Drone sends telemetry every N seconds

# Load encryption key from environment or .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
LORA_KEY_HEX = os.getenv("LORA_ENCRYPTION_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# LoRa Serial Layer
# ─────────────────────────────────────────────────────────────────────────────

class LoRaSerial:
    """
    Low-level RYLR998 serial driver for the laptop side.
    Identical protocol to the drone's lora_bridge.py.
    """

    def __init__(self, port: str, baud: int = LORA_BAUD) -> None:
        self.port = port
        self.baud = baud
        self._serial = None
        self._connected = False
        self._lock = threading.Lock()
        self._aes_key = self._load_key()

    def connect(self) -> bool:
        try:
            self._serial = serial.Serial(
                self.port, self.baud, timeout=2.0
            )
            time.sleep(0.5)

            # Configure module
            cmds = [
                f"AT+ADDRESS={LAPTOP_ADDRESS}",
                f"AT+NETWORKID={LORA_NETWORK_ID}",
                f"AT+BAND={LORA_BAND}",
                f"AT+PARAMETER={LORA_SF},{LORA_BW},{LORA_CR},{LORA_POWER}",
            ]
            for cmd in cmds:
                self._at(cmd)

            # Verify alive
            resp = self._at("AT")
            if resp and "+OK" in resp:
                self._connected = True
                return True
            return False
        except Exception as e:
            print(f"LoRa connect failed: {e}")
            return False

    def _at(self, cmd: str, timeout: float = 2.0) -> str:
        if not self._serial:
            return ""
        with self._lock:
            try:
                self._serial.write((cmd + "\r\n").encode())
                self._serial.flush()
                deadline = time.time() + timeout
                while time.time() < deadline:
                    line = self._serial.readline().decode(errors="replace").strip()
                    if line:
                        return line
            except Exception:
                pass
        return ""

    def send(self, message: str) -> bool:
        """Send encrypted message to drone (address 1)."""
        payload = f"{int(time.time())}|{LAPTOP_ADDRESS}|{message}"
        encrypted = self._encrypt(payload)
        data = encrypted or payload
        if len(data.encode()) > MAX_PAYLOAD:
            data = data[:MAX_PAYLOAD]
        cmd = f"AT+SEND={DRONE_ADDRESS},{len(data.encode())},{data}"
        resp = self._at(cmd, timeout=5.0)
        return resp is not None and "+OK" in resp

    def readline(self) -> str:
        """Read one line from the serial port. Blocking."""
        if not self._serial:
            return ""
        try:
            return self._serial.readline().decode(errors="replace").strip()
        except Exception:
            return ""

    def parse_rcv(self, line: str) -> dict | None:
        """Parse +RCV=addr,len,data,rssi,snr line."""
        if not line.startswith("+RCV="):
            return None
        try:
            content = line[5:]
            parts = content.split(",", 4)
            if len(parts) < 5:
                return None
            sender = int(parts[0])
            data_raw = parts[2]
            rssi = int(parts[3])
            snr = float(parts[4])
            plain = self._decrypt(data_raw) or data_raw
            fields = plain.split("|", 2)
            msg = fields[2] if len(fields) == 3 else plain
            return {
                "sender": sender,
                "message": msg,
                "rssi": rssi,
                "snr": snr,
                "raw": plain,
            }
        except Exception:
            return None

    def _load_key(self):
        if not LORA_KEY_HEX or not CRYPTO_AVAILABLE:
            return None
        try:
            key = bytes.fromhex(LORA_KEY_HEX)
            if len(key) in (16, 24, 32):
                return key
        except Exception:
            pass
        return None

    def _encrypt(self, text: str) -> str | None:
        if not self._aes_key or not CRYPTO_AVAILABLE:
            return None
        try:
            cipher = AES.new(self._aes_key, AES.MODE_CBC)
            ct = cipher.encrypt(pad(text.encode(), AES.block_size))
            return base64.b64encode(cipher.iv + ct).decode()
        except Exception:
            return None

    def _decrypt(self, b64: str) -> str | None:
        if not self._aes_key or not CRYPTO_AVAILABLE:
            return None
        try:
            raw = base64.b64decode(b64)
            cipher = AES.new(self._aes_key, AES.MODE_CBC, raw[:16])
            return unpad(cipher.decrypt(raw[16:]), AES.block_size).decode()
        except Exception:
            return None

    @property
    def is_connected(self):
        return self._connected

    def close(self):
        self._connected = False
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Message Protocol
# ─────────────────────────────────────────────────────────────────────────────
# The drone sends structured messages prefixed with a type tag:
#   TEL|battery=67,alt=31.2,lat=34.052,lon=-118.243,hdg=47,spd=6.2,mode=GUIDED,armed=1,gps=3,sat=14
#   ALT|level=warning,title=THREAT DETECTED,msg=person detected 94%
#   DET|class=person,conf=0.94,lat=34.052,lon=-118.243
#   THM|temp=36.8,px=142,lat=34.052,lon=-118.243
#   RPT|text=<situation report text>
#   ACK|cmd=takeoff
#   HBT|uptime=3742   (heartbeat — proves link is alive)

def parse_drone_message(raw: str) -> dict:
    """Parse a structured drone message into a typed dict."""
    if "|" not in raw:
        return {"type": "raw", "text": raw}

    tag, rest = raw.split("|", 1)
    tag = tag.strip().upper()

    result = {"type": tag, "raw": raw}

    # Parse key=value pairs
    for pair in rest.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()

    # For RPT (reports), the rest is free text after first |
    if tag == "RPT":
        result["text"] = rest

    return result

def build_command(cmd_text: str) -> str:
    """Wrap a command string in the protocol format."""
    return f"CMD|{cmd_text}"


# ─────────────────────────────────────────────────────────────────────────────
# Tkinter GUI
# ─────────────────────────────────────────────────────────────────────────────

class LoRaGroundStation:
    """
    Clean long-range LoRa ground station GUI.

    No camera feeds — just the data that actually arrives over LoRa:
    telemetry, alerts, detections, and situation reports.
    Full command control.
    """

    BG     = "#1a1a2e"
    PANEL  = "#16213e"
    ACCENT = "#0f3460"
    FG     = "#e0e0e0"
    GREEN  = "#00ff7f"
    RED    = "#ff4444"
    ORANGE = "#ff8c00"
    YELLOW = "#ffd700"
    BLUE   = "#4fc3f7"
    PURPLE = "#bb86fc"
    DIM    = "#666666"
    MONO   = ("Courier", 10)
    MONO_B = ("Courier", 10, "bold")
    MONO_S = ("Courier", 9)

    def __init__(self, lora: LoRaSerial) -> None:
        self.lora = lora
        self._running = True
        self._last_hb = time.time()
        self._det_count = 0

        self.root = tk.Tk()
        self.root.title("AURA — LoRa Long-Range Ground Terminal")
        self.root.configure(bg=self.BG)
        self.root.geometry("1000x700")
        self.root.minsize(860, 580)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()

        # Start receive thread
        self._recv_thread = threading.Thread(
            target=self._recv_loop, daemon=True
        )
        self._recv_thread.start()

        # Start watchdog
        self._watchdog()

        self._log_alert("info", "SYSTEM", "LoRa ground terminal online — long range mode active")
        self._log_alert("info", "RANGE", "Effective range: 15-20 miles line of sight")

    # ──────────────────────────────────────────
    # UI Construction
    # ──────────────────────────────────────────

    def _build_ui(self) -> None:
        # Title bar
        title = tk.Frame(self.root, bg=self.ACCENT, pady=7)
        title.pack(fill="x", padx=6, pady=(6, 0))

        tk.Label(
            title,
            text="▸ AURA  —  LONG RANGE LORA TERMINAL  ◂  915 MHz  ◂  15-20 MILES",
            bg=self.ACCENT, fg=self.BLUE,
            font=("Courier", 11, "bold")
        ).pack(side="left", padx=10)

        # Right side status indicators
        right = tk.Frame(title, bg=self.ACCENT)
        right.pack(side="right", padx=10)

        self._link_var = tk.StringVar(value="● LINK OK")
        tk.Label(right, textvariable=self._link_var,
                 bg=self.ACCENT, fg=self.GREEN, font=self.MONO_B).pack(side="right", padx=8)

        self._rssi_var = tk.StringVar(value="RSSI: ---")
        tk.Label(right, textvariable=self._rssi_var,
                 bg=self.ACCENT, fg=self.DIM, font=self.MONO_S).pack(side="right", padx=8)

        self._threat_var = tk.StringVar(value="THREAT: NONE")
        tk.Label(right, textvariable=self._threat_var,
                 bg=self.ACCENT, fg=self.GREEN, font=self.MONO_B).pack(side="right", padx=12)

        # Main layout
        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill="both", expand=True, padx=6, pady=6)
        body.columnconfigure(0, weight=0, minsize=230)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)

        # Left: telemetry
        self._build_telemetry(body)

        # Middle: alert log
        self._build_alert_log(body)

        # Right: detections + reports
        self._build_detections(body)

        # Control bar
        self._build_controls()

    def _panel(self, parent, title: str, row: int, col: int,
               rowspan=1, columnspan=1, expand=True) -> tk.Frame:
        outer = tk.Frame(parent, bg=self.PANEL, bd=1, relief="solid")
        outer.grid(row=row, col=col, padx=(0 if col > 0 else 0, 4),
                   pady=(0, 4), sticky="nsew",
                   rowspan=rowspan, columnspan=columnspan)
        tk.Label(outer, text=f"▸ {title}",
                 bg=self.PANEL, fg=self.BLUE,
                 font=("Courier", 9, "bold")).pack(anchor="w", padx=6, pady=(4, 0))
        inner = tk.Frame(outer, bg=self.PANEL)
        inner.pack(fill="both", expand=expand, padx=6, pady=(0, 6))
        return inner

    def _build_telemetry(self, parent) -> None:
        panel = self._panel(parent, "TELEMETRY  (via LoRa)", 0, 0)

        self._tel = {}
        rows = [
            ("BATTERY",  "bat",     self.GREEN),
            ("VOLTAGE",  "volt",    self.GREEN),
            ("ALTITUDE", "alt",     self.BLUE),
            ("LATITUDE", "lat",     self.FG),
            ("LONGITUDE","lon",     self.FG),
            ("HEADING",  "hdg",     self.BLUE),
            ("SPEED",    "spd",     self.BLUE),
            ("MODE",     "mode",    self.PURPLE),
            ("GPS FIX",  "gps",     self.GREEN),
            ("ARMED",    "armed",   self.GREEN),
            ("SATELLITES","sat",    self.GREEN),
            ("FLIGHT",   "time",    self.ORANGE),
        ]

        self._flight_start = None

        for label, key, color in rows:
            f = tk.Frame(panel, bg=self.PANEL)
            f.pack(fill="x", pady=1)
            tk.Label(f, text=f"{label}:", width=11, anchor="w",
                     bg=self.PANEL, fg=self.DIM,
                     font=self.MONO_S).pack(side="left")
            var = tk.StringVar(value="---")
            self._tel[key] = var
            tk.Label(f, textvariable=var, bg=self.PANEL,
                     fg=color, font=self.MONO_B,
                     anchor="w").pack(side="left")

        # Battery bar
        tk.Label(panel, text="", bg=self.PANEL).pack(pady=2)
        bat_frame = tk.Frame(panel, bg=self.PANEL)
        bat_frame.pack(fill="x")
        tk.Label(bat_frame, text="BAT:", bg=self.PANEL,
                 fg=self.DIM, font=self.MONO_S).pack(side="left")
        self._bat_canvas = tk.Canvas(bat_frame, bg="#0f3460",
                                      height=10, bd=0, highlightthickness=0)
        self._bat_canvas.pack(side="left", fill="x", expand=True, padx=4)
        self._bat_bar = self._bat_canvas.create_rectangle(
            0, 0, 0, 10, fill=self.GREEN, outline=""
        )

        # Link quality
        tk.Label(panel, text="", bg=self.PANEL).pack(pady=2)
        lq_frame = tk.Frame(panel, bg=self.PANEL)
        lq_frame.pack(fill="x")
        tk.Label(lq_frame, text="LINK:", bg=self.PANEL,
                 fg=self.DIM, font=self.MONO_S).pack(side="left")
        self._snr_var = tk.StringVar(value="SNR: ---  RSSI: ---")
        tk.Label(lq_frame, textvariable=self._snr_var,
                 bg=self.PANEL, fg=self.BLUE,
                 font=self.MONO_S).pack(side="left", padx=4)

        # WiFi switch prompt
        tk.Frame(panel, bg=self.PANEL, height=8).pack()
        self._wifi_btn = tk.Button(
            panel,
            text="⟳ SWITCH TO WIFI (fly closer)",
            command=self._request_wifi_mode,
            bg="#0f3460", fg=self.YELLOW,
            font=("Courier", 9, "bold"),
            relief="raised", pady=4, cursor="hand2",
        )
        self._wifi_btn.pack(fill="x", padx=2)
        tk.Label(panel,
                 text="sends drone to relay altitude\nthen broadcasts WiFi hotspot",
                 bg=self.PANEL, fg=self.DIM,
                 font=("Courier", 8), justify="center").pack()

    def _build_alert_log(self, parent) -> None:
        panel = self._panel(parent, "ALERT LOG", 0, 1)
        self._alert_text = scrolledtext.ScrolledText(
            panel, bg="#0d0d1a", fg=self.FG,
            font=self.MONO_S, state="disabled", wrap="word",
        )
        self._alert_text.pack(fill="both", expand=True)
        for tag, color in [
            ("critical", self.RED),
            ("error",    self.ORANGE),
            ("warning",  self.YELLOW),
            ("info",     self.FG),
            ("dim",      self.DIM),
        ]:
            weight = "bold" if tag == "critical" else "normal"
            self._alert_text.tag_config(tag, foreground=color,
                                         font=("Courier", 9, weight))

    def _build_detections(self, parent) -> None:
        panel = self._panel(parent, "DETECTIONS  &  REPORTS", 0, 2)

        # Detection log
        tk.Label(panel, text="— Visual / Thermal —",
                 bg=self.PANEL, fg=self.DIM,
                 font=("Courier", 8)).pack(anchor="w")
        self._det_text = scrolledtext.ScrolledText(
            panel, bg="#0d0d1a", fg=self.FG,
            font=self.MONO_S, state="disabled", wrap="word", height=10,
        )
        self._det_text.pack(fill="x")
        self._det_text.tag_config("person",  foreground=self.RED)
        self._det_text.tag_config("vehicle", foreground=self.ORANGE)
        self._det_text.tag_config("thermal", foreground="#ff6600")
        self._det_text.tag_config("resource",foreground=self.BLUE)
        self._det_text.tag_config("default", foreground=self.FG)

        self._det_count_var = tk.StringVar(value="Total detections: 0")
        tk.Label(panel, textvariable=self._det_count_var,
                 bg=self.PANEL, fg=self.DIM,
                 font=("Courier", 8)).pack(anchor="e")

        # Situation report box
        tk.Label(panel, text="— Latest Sitrep —",
                 bg=self.PANEL, fg=self.DIM,
                 font=("Courier", 8)).pack(anchor="w", pady=(8, 0))
        self._rpt_text = scrolledtext.ScrolledText(
            panel, bg="#0d0d1a", fg=self.GREEN,
            font=self.MONO_S, state="disabled", wrap="word", height=6,
        )
        self._rpt_text.pack(fill="both", expand=True)

        # Request sitrep button
        tk.Button(
            panel,
            text="📋 REQUEST SITREP FROM DRONE",
            command=lambda: self._send_command("situation report"),
            bg="#0f3460", fg="#80cbc4",
            font=("Courier", 9, "bold"),
            relief="raised", pady=3, cursor="hand2",
        ).pack(fill="x", pady=(4, 0))

    def _build_controls(self) -> None:
        bar = tk.Frame(self.root, bg=self.ACCENT, pady=7)
        bar.pack(fill="x", padx=6, pady=(0, 6))

        btn = dict(
            bg=self.BG, font=("Courier", 9, "bold"),
            relief="raised", padx=7, pady=5,
            cursor="hand2", bd=1,
        )

        buttons = [
            ("▲ TAKEOFF",   lambda: self._send_command("takeoff 30"),    self.GREEN),
            ("▼ LAND",      lambda: self._send_command("land"),           self.ORANGE),
            ("⟲ RTH",       lambda: self._send_command("return home"),    self.YELLOW),
            ("⏸ HOVER",     lambda: self._send_command("hover"),          self.BLUE),
            ("⟳ PATROL",    lambda: self._send_command("patrol"),         self.PURPLE),
            ("⊕ SCOUT",     lambda: self._send_command("scout"),          "#cf6679"),
            ("📋 SITREP",   lambda: self._send_command("situation report"),"#80cbc4"),
            ("DROP",        lambda: self._send_command("drop payload"),    self.ORANGE),
        ]

        for label, cmd, color in buttons:
            b = tk.Button(bar, text=label, command=cmd, fg=color, **btn)
            b.pack(side="left", padx=2)

        tk.Frame(bar, bg=self.ACCENT, width=10).pack(side="left")
        tk.Label(bar, text="CMD:", bg=self.ACCENT,
                 fg=self.FG, font=self.MONO_S).pack(side="left")

        self._cmd_entry = tk.Entry(
            bar, bg="#0d0d1a", fg=self.GREEN,
            font=("Courier", 11), width=30,
            insertbackground=self.GREEN
        )
        self._cmd_entry.pack(side="left", padx=4)
        self._cmd_entry.bind("<Return>", lambda e: self._send_raw())

        tk.Button(bar, text="SEND", command=self._send_raw,
                  fg=self.GREEN, **btn).pack(side="left", padx=2)

        # Range reminder
        tk.Label(
            bar,
            text="LoRa  ◂  text only ◂  15-20 mi",
            bg=self.ACCENT, fg=self.DIM,
            font=("Courier", 8)
        ).pack(side="right", padx=10)

    # ──────────────────────────────────────────
    # Receive Loop
    # ──────────────────────────────────────────

    def _recv_loop(self) -> None:
        """Background thread — read LoRa messages and update GUI."""
        while self._running:
            line = self.lora.readline()
            if not line:
                continue

            if line.startswith("+RCV="):
                parsed = self.lora.parse_rcv(line)
                if parsed:
                    self._last_hb = time.time()
                    rssi = parsed["rssi"]
                    snr = parsed["snr"]
                    self.root.after(0, self._rssi_var.set,
                                    f"RSSI:{rssi} SNR:{snr:+.1f}")
                    self.root.after(0, self._process_message,
                                    parsed["message"], rssi, snr)

    def _process_message(self, msg: str, rssi: int, snr: float) -> None:
        """Dispatch incoming drone message to the right UI element."""
        parsed = parse_drone_message(msg)
        mtype = parsed.get("type", "RAW")

        if mtype == "TEL":
            self._update_telemetry(parsed, rssi, snr)

        elif mtype == "ALT":
            level = parsed.get("level", "info")
            title = parsed.get("title", "ALERT")
            text  = parsed.get("msg",   parsed.get("text", ""))
            self._log_alert(level, title, text)
            # Update threat indicator
            if level in ("warning", "error", "critical"):
                threat = {"critical":"CRITICAL","error":"HIGH","warning":"MEDIUM"}.get(level,"LOW")
                color  = {"CRITICAL":self.RED,"HIGH":self.RED,
                          "MEDIUM":self.YELLOW,"LOW":self.GREEN}.get(threat, self.GREEN)
                self._threat_var.set(f"THREAT: {threat}")

        elif mtype == "DET":
            self._add_detection(parsed)

        elif mtype == "THM":
            self._add_thermal(parsed)

        elif mtype == "RPT":
            self._update_report(parsed.get("text", msg))

        elif mtype == "ACK":
            self._log_alert("dim", "ACK",
                            f"Drone confirmed: {parsed.get('cmd','?')}")

        elif mtype == "HBT":
            self._log_alert("dim", "HBT",
                            f"Drone heartbeat  uptime={parsed.get('uptime','?')}s  "
                            f"RSSI={rssi}  SNR={snr:+.1f}")

        else:
            self._log_alert("dim", "RAW", msg[:120])

    # ──────────────────────────────────────────
    # UI Updaters
    # ──────────────────────────────────────────

    def _update_telemetry(self, d: dict, rssi: int, snr: float) -> None:
        bat = int(d.get("battery", d.get("bat", 0)))
        bat_color = (self.RED if bat <= 15 else
                     self.ORANGE if bat <= 30 else self.GREEN)

        self._tel["bat"].set(f"{bat}%")
        self._tel["volt"].set(d.get("volt", d.get("voltage", "---")))
        self._tel["alt"].set(f"{float(d.get('alt', 0)):.1f} m")
        self._tel["lat"].set(f"{d.get('lat', '---')}°")
        self._tel["lon"].set(f"{d.get('lon', '---')}°")
        self._tel["hdg"].set(f"{d.get('hdg', '---')}°")
        self._tel["spd"].set(f"{float(d.get('spd', 0)):.1f} m/s")
        self._tel["mode"].set(d.get("mode", "---"))
        self._tel["gps"].set(f"FIX {d.get('gps', '?')}")
        self._tel["armed"].set("YES" if d.get("armed", "0") == "1" else "NO")
        self._tel["sat"].set(d.get("sat", "---"))
        self._snr_var.set(f"SNR:{snr:+.1f}  RSSI:{rssi}")

        # Flight timer
        armed = d.get("armed", "0") == "1"
        if armed and not self._flight_start:
            self._flight_start = time.time()
        elif not armed:
            self._flight_start = None

        if self._flight_start:
            e = int(time.time() - self._flight_start)
            self._tel["time"].set(f"{e//60:02d}:{e%60:02d}")
        else:
            self._tel["time"].set("00:00")

        # Battery bar
        self._bat_canvas.update_idletasks()
        w = self._bat_canvas.winfo_width()
        bar_w = int(w * (bat / 100))
        self._bat_canvas.coords(self._bat_bar, 0, 0, bar_w, 10)
        self._bat_canvas.itemconfig(self._bat_bar, fill=bat_color)

    def _log_alert(self, level: str, title: str, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level.upper():8s}] {title}: {msg}\n"
        self._alert_text.configure(state="normal")
        self._alert_text.insert("end", line,
                                level if level in ("critical","error","warning","info","dim")
                                else "info")
        self._alert_text.configure(state="disabled")
        self._alert_text.see("end")

    def _add_detection(self, d: dict) -> None:
        cls  = d.get("class", d.get("cls", "?"))
        conf = d.get("conf", "?")
        lat  = d.get("lat", "")
        lon  = d.get("lon", "")
        ts   = datetime.now().strftime("%H:%M:%S")
        loc  = f" @ ({lat},{lon})" if lat else ""
        line = f"[{ts}] {cls.upper()} {conf}{loc}\n"

        tag = ("person"  if cls in ("person",) else
               "vehicle" if cls in ("car","truck","motorcycle") else
               "resource" if cls in ("backpack","boat","bicycle") else "default")

        self._det_text.configure(state="normal")
        self._det_text.insert("end", line, tag)
        self._det_text.configure(state="disabled")
        self._det_text.see("end")
        self._det_count += 1
        self._det_count_var.set(f"Total detections: {self._det_count}")

    def _add_thermal(self, d: dict) -> None:
        temp = d.get("temp", "?")
        px   = d.get("px",   "?")
        lat  = d.get("lat",  "")
        lon  = d.get("lon",  "")
        ts   = datetime.now().strftime("%H:%M:%S")
        loc  = f" @ ({lat},{lon})" if lat else ""
        line = f"[{ts}] THERMAL {temp}°C  {px}px blob{loc}\n"
        self._det_text.configure(state="normal")
        self._det_text.insert("end", line, "thermal")
        self._det_text.configure(state="disabled")
        self._det_text.see("end")
        self._det_count += 1
        self._det_count_var.set(f"Total detections: {self._det_count}")

    def _update_report(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._rpt_text.configure(state="normal")
        self._rpt_text.delete("1.0", "end")
        self._rpt_text.insert("end", f"[{ts}]\n{text}\n")
        self._rpt_text.configure(state="disabled")
        self._log_alert("info", "SITREP RECEIVED", "See report panel →")

    # ──────────────────────────────────────────
    # Commands
    # ──────────────────────────────────────────

    def _send_command(self, text: str) -> None:
        if not self.lora.is_connected:
            messagebox.showwarning("Not Connected", "LoRa module not connected.")
            return
        ok = self.lora.send(build_command(text))
        level = "info" if ok else "error"
        self._log_alert(level, "CMD SENT" if ok else "SEND FAIL", text)

    def _send_raw(self) -> None:
        text = self._cmd_entry.get().strip()
        if text:
            self._cmd_entry.delete(0, "end")
            self._send_command(text)

    def _request_wifi_mode(self) -> None:
        """Tell the drone to fly to relay altitude and start WiFi hotspot."""
        if messagebox.askyesno(
            "Switch to WiFi",
            "This will command the drone to:\n\n"
            "1. Fly to relay altitude (60m)\n"
            "2. Start the WiFi hotspot (AURA-RELAY)\n"
            "3. Hold position\n\n"
            "Fly your drone within ~1 mile first.\n"
            "Then connect your laptop to 'AURA-RELAY' WiFi.\n\n"
            "Continue?"
        ):
            self._send_command("relay mode")
            self._log_alert(
                "warning", "WIFI MODE",
                "Drone switching to WiFi relay — "
                "connect to 'AURA-RELAY' hotspot then open full GUI"
            )
            messagebox.showinfo(
                "Next Step",
                "Command sent!\n\n"
                "Wait for drone to reach relay altitude (~30s),\n"
                "then connect your laptop to WiFi: AURA-RELAY\n"
                "then close this terminal and open the full ground station."
            )

    # ──────────────────────────────────────────
    # Watchdog
    # ──────────────────────────────────────────

    def _watchdog(self) -> None:
        """Check if we've received data recently — warn on link timeout."""
        elapsed = time.time() - self._last_hb
        if elapsed > 30:
            self._link_var.set("● LINK TIMEOUT")
            # update the label color via a workaround
        elif elapsed > 15:
            self._link_var.set("● LINK WEAK")
        else:
            self._link_var.set("● LINK OK")

        self.root.after(5000, self._watchdog)

    # ──────────────────────────────────────────

    def _on_close(self) -> None:
        self._running = False
        self.lora.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
# Auto-detect COM port
# ─────────────────────────────────────────────────────────────────────────────

def find_lora_port() -> str | None:
    """Scan serial ports for a likely RYLR998 module."""
    ports = list(serial.tools.list_ports.comports())
    print(f"Found {len(ports)} serial port(s):")

    candidates = []
    for p in ports:
        desc = (p.description or "").lower()
        mfr  = (p.manufacturer or "").lower()
        print(f"  {p.device:20s} — {p.description}")
        # CP2102 / CH340 / FTDI are common USB-UART chips
        if any(x in desc or x in mfr for x in
               ["cp210", "ch340", "ftdi", "uart", "serial", "usb-serial"]):
            candidates.append(p.device)

    if len(candidates) == 1:
        print(f"\nAuto-selected: {candidates[0]}")
        return candidates[0]
    if len(candidates) > 1:
        print("\nMultiple candidates found. Specify with --port")
        for i, c in enumerate(candidates):
            print(f"  {i+1}. {c}")
        try:
            n = int(input("Select (number): ")) - 1
            return candidates[n]
        except Exception:
            return candidates[0]

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AURA LoRa Long-Range Ground Terminal"
    )
    parser.add_argument("--port", help="Serial port for LoRa module (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=LORA_BAUD)
    args = parser.parse_args()

    print("=" * 60)
    print("  AURA DRONE — LoRa Long-Range Ground Terminal")
    print("  Range: 15-20 miles  |  Text telemetry + commands")
    print("=" * 60)

    # Find port
    port = args.port or find_lora_port()
    if not port:
        print("\nERROR: No LoRa module found.")
        print("Connect your RYLR998 via CP2102 USB-UART adapter")
        print("then specify: python lora_client.py --port COM3")
        sys.exit(1)

    # Connect
    lora = LoRaSerial(port, args.baud)
    print(f"\nConnecting to LoRa module on {port}...")
    if not lora.connect():
        print("ERROR: Could not communicate with RYLR998 module.")
        print("Check wiring: VDD=3.3V, TX→RX, RX→TX, GND→GND")
        sys.exit(1)

    print("LoRa module OK — waiting for drone messages...\n")

    if not CRYPTO_AVAILABLE:
        print("WARNING: pycryptodome not installed — messages will be unencrypted")
        print("         Install: pip install pycryptodome\n")

    if not LORA_KEY_HEX:
        print("WARNING: No LORA_ENCRYPTION_KEY in .env — messages unencrypted")
        print("         Add key to .env file (must match drone's key)\n")

    # Launch GUI
    if GUI_AVAILABLE:
        app = LoRaGroundStation(lora)
        app.run()
    else:
        print("Tkinter not available — running CLI mode")
        print("Commands: takeoff <alt>, land, rth, hover, patrol, scout, sitrep, quit\n")
        while lora.is_connected:
            try:
                text = input("CMD> ").strip()
                if not text:
                    continue
                if text.lower() in ("quit", "exit"):
                    break
                lora.send(build_command(text))
                print("  Sent.")
            except (KeyboardInterrupt, EOFError):
                break
        lora.close()


if __name__ == "__main__":
    main()
