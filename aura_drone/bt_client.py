"""
bt_client.py — AURA Bluetooth Ground Station Client

Run this on your LAPTOP to connect to the AURA drone via Bluetooth.
No WiFi, no router, no internet — just Bluetooth.

Usage:
    python bt_client.py                    # Auto-scan and connect
    python bt_client.py --mac AA:BB:CC:DD:EE:FF   # Connect to known drone MAC
    python bt_client.py --scan             # Just scan and list nearby AURA drones

Requirements (laptop):
    pip install PyBluez windows-curses     # Windows
    pip install PyBluez                    # Linux / Mac
    sudo apt install bluetooth bluez python3-bluetooth  # Linux system deps

Pairing (first time only):
    Linux:   bluetoothctl → scan on → pair <MAC> → trust <MAC>
    Windows: Settings → Bluetooth → Add Device → select "AURA Drone"
    Mac:     System Preferences → Bluetooth → pair AURA Drone
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime

# ─────────────────────────────────────────────
# Bluetooth import
# ─────────────────────────────────────────────
try:
    import bluetooth
    BLUETOOTH_AVAILABLE = True
except ImportError:
    print("ERROR: PyBluez not installed.")
    print("Install: pip install PyBluez")
    print("Linux also needs: sudo apt install bluetooth bluez python3-bluetooth")
    sys.exit(1)

# ─────────────────────────────────────────────
# Tkinter GUI import
# ─────────────────────────────────────────────
try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

AURA_BT_UUID = "94f39d29-7d6d-437d-973b-fba39e49d4ee"
AURA_SERVICE_NAME = "AURA Drone Ground Station"


# ─────────────────────────────────────────────────────────────────────────────
# Connection Layer
# ─────────────────────────────────────────────────────────────────────────────

class AURAConnection:
    """
    Manages the Bluetooth RFCOMM connection to the drone.
    Runs receive loop in a background thread.
    Fires callbacks on incoming data packets.
    """

    def __init__(self, on_packet=None, on_disconnect=None) -> None:
        self.on_packet = on_packet
        self.on_disconnect = on_disconnect
        self._sock = None
        self._connected = False
        self._recv_thread = None
        self._buffer = ""

    def scan(self) -> list[dict]:
        """
        Scan for nearby AURA drones advertising the RFCOMM service.

        Returns:
            List of {"name": str, "mac": str, "channel": int}
        """
        print("Scanning for AURA drones... (10 seconds)")
        found = []

        try:
            # Try SDP service discovery first (finds service by UUID)
            services = bluetooth.find_service(uuid=AURA_BT_UUID)
            for svc in services:
                found.append({
                    "name": svc.get("name", "AURA Drone"),
                    "mac": svc["host"],
                    "channel": svc["port"],
                })
                print(f"  Found: {svc.get('name','AURA Drone')} @ {svc['host']} ch{svc['port']}")
        except Exception:
            pass

        # Fallback: scan all nearby devices, filter by name
        if not found:
            try:
                nearby = bluetooth.discover_devices(duration=8, lookup_names=True)
                for mac, name in nearby:
                    if "AURA" in (name or "").upper():
                        found.append({"name": name, "mac": mac, "channel": 1})
                        print(f"  Found by name: {name} @ {mac}")
            except Exception as e:
                print(f"  Scan error: {e}")

        if not found:
            print("  No AURA drones found nearby.")

        return found

    def connect(self, mac: str, channel: int = 1) -> bool:
        """
        Connect to a drone by MAC address.

        Args:
            mac: Bluetooth MAC address (AA:BB:CC:DD:EE:FF)
            channel: RFCOMM channel (default 1)

        Returns:
            True if connected
        """
        print(f"Connecting to {mac} channel {channel}...")
        try:
            sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
            sock.settimeout(15)
            sock.connect((mac, channel))
            sock.settimeout(None)  # Back to blocking after connect
            self._sock = sock
            self._connected = True

            # Start receive thread
            self._recv_thread = threading.Thread(
                target=self._receive_loop, daemon=True
            )
            self._recv_thread.start()

            print(f"Connected to AURA drone at {mac}")
            return True

        except bluetooth.btcommon.BluetoothError as e:
            print(f"Connection failed: {e}")
            print("Make sure you've paired the drone first.")
            return False
        except Exception as e:
            print(f"Connection error: {e}")
            return False

    def send_command(self, cmd: str, params: dict = None, raw_text: str = None) -> bool:
        """
        Send a command to the drone.

        Args:
            cmd: Command name ("takeoff", "land", "return_home", etc.)
            params: Optional parameters dict
            raw_text: Raw text command (AI will parse it)
        """
        if not self._connected:
            return False

        packet = {}
        if raw_text:
            packet = {"cmd": "raw", "text": raw_text}
        else:
            packet = {"cmd": cmd, "params": params or {}}

        try:
            line = json.dumps(packet) + "\n"
            self._sock.send(line.encode("utf-8"))
            return True
        except Exception as e:
            print(f"Send failed: {e}")
            self._on_disconnect()
            return False

    def disconnect(self) -> None:
        """Close connection."""
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def _receive_loop(self) -> None:
        """Background thread — reads incoming JSON lines from drone."""
        while self._connected:
            try:
                chunk = self._sock.recv(2048).decode("utf-8", errors="replace")
                if not chunk:
                    self._on_disconnect()
                    return

                self._buffer += chunk
                while "\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            packet = json.loads(line)
                            if self.on_packet:
                                self.on_packet(packet)
                        except json.JSONDecodeError:
                            pass

            except OSError:
                self._on_disconnect()
                return
            except Exception as e:
                if self._connected:
                    print(f"Receive error: {e}")

    def _on_disconnect(self) -> None:
        self._connected = False
        if self.on_disconnect:
            self.on_disconnect()

    @property
    def is_connected(self) -> bool:
        return self._connected


# ─────────────────────────────────────────────────────────────────────────────
# Tkinter GUI Ground Station
# ─────────────────────────────────────────────────────────────────────────────

class BTGroundStation:
    """
    Laptop-side Bluetooth ground station GUI.

    Displays:
    - Connection status + drone MAC
    - Full telemetry panel (battery, altitude, GPS, mode, etc.)
    - Scrolling alert log with color coding
    - Detection log (YOLO + thermal)
    - Full command button bar + text input
    - Threat level indicator

    All data comes over Bluetooth JSON stream from the Jetson.
    """

    BG = "#1a1a2e"
    PANEL = "#16213e"
    ACCENT = "#0f3460"
    FG = "#e0e0e0"
    GREEN = "#00ff7f"
    RED = "#ff4444"
    ORANGE = "#ff8c00"
    YELLOW = "#ffd700"
    BLUE = "#4fc3f7"
    PURPLE = "#bb86fc"
    DIM = "#888888"
    MONO = ("Courier", 10)
    MONO_SM = ("Courier", 9)
    BOLD = ("Courier", 10, "bold")

    def __init__(self, connection: AURAConnection) -> None:
        self.conn = connection
        self.root = tk.Tk()
        self.root.title("AURA — Bluetooth Ground Station")
        self.root.configure(bg=self.BG)
        self.root.geometry("900x680")
        self.root.minsize(800, 580)

        # Register packet handler
        self.conn.on_packet = self._on_packet
        self.conn.on_disconnect = self._on_disconnect

        # State
        self._last_telemetry = {}
        self._threat_level = "NONE"
        self._flight_start: float = None
        self._detection_count = 0

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Periodic clock update
        self._tick()

    def _build_ui(self) -> None:
        # ── Title bar ─────────────────────────
        title_bar = tk.Frame(self.root, bg=self.ACCENT, pady=6)
        title_bar.pack(fill="x", padx=6, pady=(6, 0))

        tk.Label(
            title_bar,
            text="▸ AURA DRONE  —  BLUETOOTH GROUND STATION",
            bg=self.ACCENT, fg=self.BLUE,
            font=("Courier", 12, "bold"), anchor="w"
        ).pack(side="left", padx=10)

        self._conn_var = tk.StringVar(value="● CONNECTED")
        tk.Label(
            title_bar, textvariable=self._conn_var,
            bg=self.ACCENT, fg=self.GREEN,
            font=self.BOLD
        ).pack(side="right", padx=10)

        self._threat_var = tk.StringVar(value="THREAT: NONE")
        tk.Label(
            title_bar, textvariable=self._threat_var,
            bg=self.ACCENT, fg=self.GREEN,
            font=self.BOLD
        ).pack(side="right", padx=20)

        # ── Main content area ──────────────────
        content = tk.Frame(self.root, bg=self.BG)
        content.pack(fill="both", expand=True, padx=6, pady=6)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        # Left column: telemetry + detections
        left = tk.Frame(content, bg=self.BG)
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 4))
        self._build_telemetry(left)
        self._build_detections(left)

        # Right column: alert log
        right = tk.Frame(content, bg=self.BG)
        right.grid(row=0, column=1, sticky="nsew")
        self._build_alert_log(right)

        # ── Control bar ───────────────────────
        self._build_controls()

    def _panel_frame(self, parent, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg=self.PANEL, bd=1, relief="solid")
        outer.pack(fill="both", expand=True, pady=(0, 5))
        tk.Label(
            outer, text=f"▸ {title}",
            bg=self.PANEL, fg=self.BLUE,
            font=("Courier", 9, "bold")
        ).pack(anchor="w", padx=6, pady=(4, 0))
        inner = tk.Frame(outer, bg=self.PANEL)
        inner.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        return inner

    def _build_telemetry(self, parent) -> None:
        panel = self._panel_frame(parent, "TELEMETRY")

        fields = [
            ("BATTERY",   "tel_battery",  self.GREEN),
            ("VOLTAGE",   "tel_voltage",  self.GREEN),
            ("ALTITUDE",  "tel_altitude", self.BLUE),
            ("LATITUDE",  "tel_lat",      self.FG),
            ("LONGITUDE", "tel_lon",      self.FG),
            ("HEADING",   "tel_heading",  self.BLUE),
            ("SPEED",     "tel_speed",    self.BLUE),
            ("MODE",      "tel_mode",     self.PURPLE),
            ("GPS FIX",   "tel_gps",      self.GREEN),
            ("ARMED",     "tel_armed",    self.GREEN),
            ("FLIGHT",    "tel_time",     self.ORANGE),
        ]

        self._tel_vars = {}
        for label, key, color in fields:
            row = tk.Frame(panel, bg=self.PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(
                row, text=f"{label}:", width=10, anchor="w",
                bg=self.PANEL, fg=self.DIM, font=self.MONO_SM
            ).pack(side="left")
            var = tk.StringVar(value="---")
            self._tel_vars[key] = var
            tk.Label(
                row, textvariable=var,
                bg=self.PANEL, fg=color,
                font=self.BOLD, anchor="w"
            ).pack(side="left")

    def _build_detections(self, parent) -> None:
        panel = self._panel_frame(parent, "DETECTIONS")
        self._det_text = scrolledtext.ScrolledText(
            panel, bg="#0d0d1a", fg=self.FG,
            font=self.MONO_SM, height=8,
            state="disabled", wrap="word",
        )
        self._det_text.pack(fill="both", expand=True)
        self._det_text.tag_config("threat", foreground=self.RED)
        self._det_text.tag_config("thermal", foreground=self.ORANGE)
        self._det_text.tag_config("resource", foreground=self.BLUE)
        self._det_text.tag_config("normal", foreground=self.FG)

        self._det_count_var = tk.StringVar(value="Total: 0")
        tk.Label(
            panel, textvariable=self._det_count_var,
            bg=self.PANEL, fg=self.DIM, font=self.MONO_SM
        ).pack(anchor="e")

    def _build_alert_log(self, parent) -> None:
        panel = self._panel_frame(parent, "ALERT LOG")
        self._alert_text = scrolledtext.ScrolledText(
            panel, bg="#0d0d1a", fg=self.FG,
            font=self.MONO_SM, state="disabled", wrap="word",
        )
        self._alert_text.pack(fill="both", expand=True)
        self._alert_text.tag_config("critical", foreground=self.RED, font=("Courier", 9, "bold"))
        self._alert_text.tag_config("error",    foreground=self.ORANGE)
        self._alert_text.tag_config("warning",  foreground=self.YELLOW)
        self._alert_text.tag_config("info",     foreground=self.FG)

    def _build_controls(self) -> None:
        bar = tk.Frame(self.root, bg=self.ACCENT, pady=7)
        bar.pack(fill="x", padx=6, pady=(0, 6))

        btn_cfg = dict(
            bg=self.BG, font=("Courier", 9, "bold"),
            relief="raised", padx=8, pady=5, cursor="hand2", bd=1,
        )

        buttons = [
            ("▲ TAKEOFF",  lambda: self._send("takeoff", {"altitude": 30}), self.GREEN),
            ("▼ LAND",     lambda: self._send("land"),                       self.ORANGE),
            ("⟲ RTH",      lambda: self._send("return_home"),                self.YELLOW),
            ("⏸ HOVER",    lambda: self._send("hover"),                      self.BLUE),
            ("⟳ PATROL",   lambda: self._send("patrol"),                     self.PURPLE),
            ("⊕ SCOUT",    lambda: self._send("scout"),                      "#cf6679"),
            ("📋 SITREP",  lambda: self._send("situation_report"),           "#80cbc4"),
        ]

        for label, cmd, color in buttons:
            b = tk.Button(bar, text=label, command=cmd, fg=color, **btn_cfg)
            b.pack(side="left", padx=2)

        tk.Frame(bar, bg=self.ACCENT, width=15).pack(side="left")

        tk.Label(bar, text="CMD:", bg=self.ACCENT, fg=self.FG,
                 font=("Courier", 9, "bold")).pack(side="left")

        self._cmd_entry = tk.Entry(
            bar, bg="#0d0d1a", fg=self.GREEN,
            font=("Courier", 11), width=32,
            insertbackground=self.GREEN
        )
        self._cmd_entry.pack(side="left", padx=4)
        self._cmd_entry.bind("<Return>", lambda e: self._send_raw())

        tk.Button(
            bar, text="SEND", command=self._send_raw,
            fg=self.GREEN, **btn_cfg
        ).pack(side="left", padx=2)

        # BT status
        self._bt_status_var = tk.StringVar(value="BT: LIVE")
        tk.Label(
            bar, textvariable=self._bt_status_var,
            bg=self.ACCENT, fg=self.GREEN,
            font=self.BOLD
        ).pack(side="right", padx=10)

    # ──────────────────────────────────────────
    # Packet Handlers
    # ──────────────────────────────────────────

    def _on_packet(self, packet: dict) -> None:
        """Route incoming packet to the right handler — called from recv thread."""
        # Schedule GUI update on main thread (Tkinter is not thread-safe)
        self.root.after(0, self._process_packet, packet)

    def _process_packet(self, packet: dict) -> None:
        """Process packet on main thread."""
        ptype = packet.get("type", "")

        if ptype == "telemetry":
            self._update_telemetry(packet.get("data", {}))
        elif ptype == "alert":
            self._add_alert(packet.get("data", {}))
        elif ptype == "detections":
            self._add_detections(packet.get("data", []))
        elif ptype == "thermal_alerts":
            self._add_thermal_alerts(packet.get("data", []))
        elif ptype == "welcome":
            self._log_alert({
                "level": "info",
                "title": "CONNECTED",
                "message": packet.get("message", "Drone connected"),
                "datetime": datetime.now().strftime("%H:%M:%S"),
            })
        elif ptype == "ack":
            self._log_alert({
                "level": "info",
                "title": "ACK",
                "message": f"Command accepted: {packet.get('cmd', '?')}",
                "datetime": datetime.now().strftime("%H:%M:%S"),
            })
        elif ptype == "error":
            self._log_alert({
                "level": "error",
                "title": "ERROR",
                "message": packet.get("message", "Unknown error"),
                "datetime": datetime.now().strftime("%H:%M:%S"),
            })

    def _update_telemetry(self, tel: dict) -> None:
        """Refresh telemetry panel values."""
        self._last_telemetry = tel

        bat = tel.get("battery_percent", 0)
        bat_color = self.RED if bat <= 15 else self.ORANGE if bat <= 30 else self.GREEN

        self._tel_vars["tel_battery"].set(f"{bat}%")
        self._tel_vars["tel_voltage"].set(f"{tel.get('battery_voltage', 0):.1f}V")
        self._tel_vars["tel_altitude"].set(f"{tel.get('altitude_m', 0):.1f} m")
        self._tel_vars["tel_lat"].set(f"{tel.get('latitude', 0):.5f}°")
        self._tel_vars["tel_lon"].set(f"{tel.get('longitude', 0):.5f}°")
        self._tel_vars["tel_heading"].set(f"{tel.get('heading_deg', 0):.0f}°")
        self._tel_vars["tel_speed"].set(f"{tel.get('groundspeed_ms', 0):.1f} m/s")
        self._tel_vars["tel_mode"].set(tel.get("mode", "?"))
        self._tel_vars["tel_gps"].set(
            f"FIX {tel.get('gps_fix', 0)} ({tel.get('satellites', 0)} sat)"
        )
        self._tel_vars["tel_armed"].set("YES" if tel.get("armed") else "NO")

        # Flight timer
        if tel.get("armed") and not self._flight_start:
            self._flight_start = time.time()
        elif not tel.get("armed"):
            self._flight_start = None

        if self._flight_start:
            e = int(time.time() - self._flight_start)
            self._tel_vars["tel_time"].set(f"{e//60:02d}:{e%60:02d}")
        else:
            self._tel_vars["tel_time"].set("00:00")

    def _add_alert(self, alert: dict) -> None:
        """Add an alert from the drone to the alert log."""
        self._log_alert(alert)

        # Update threat level if assessment alert
        if "THREAT" in alert.get("title", "").upper():
            level = alert.get("level", "info")
            color_map = {
                "critical": (self.RED, "CRITICAL"),
                "error": (self.RED, "HIGH"),
                "warning": (self.YELLOW, "MEDIUM"),
                "info": (self.GREEN, "LOW"),
            }
            color, text = color_map.get(level, (self.GREEN, "LOW"))
            self._threat_var.set(f"THREAT: {text}")

    def _log_alert(self, alert: dict) -> None:
        """Write alert line to the scrolling log."""
        level = alert.get("level", "info")
        dt = alert.get("datetime", datetime.now().strftime("%H:%M:%S"))
        title = alert.get("title", "?")
        msg = alert.get("message", "")

        line = f"[{dt}] [{level.upper():8s}] {title}: {msg}\n"

        self._alert_text.configure(state="normal")
        self._alert_text.insert("end", line, level if level in ("critical","error","warning","info") else "info")
        self._alert_text.configure(state="disabled")
        self._alert_text.see("end")

    def _add_detections(self, detections: list) -> None:
        """Log YOLO detections to detection panel."""
        for det in detections:
            cls = det.get("class", "?")
            conf = det.get("confidence", 0)
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {cls.upper()} {conf:.0%}\n"

            tag = "threat" if cls in ("person","car","truck","motorcycle") else \
                  "resource" if cls in ("backpack","boat","bicycle") else "normal"

            self._det_text.configure(state="normal")
            self._det_text.insert("end", line, tag)
            self._det_text.configure(state="disabled")
            self._det_text.see("end")
            self._detection_count += 1

        self._det_count_var.set(f"Total detections: {self._detection_count}")

    def _add_thermal_alerts(self, alerts: list) -> None:
        """Log thermal alerts to detection panel."""
        for alert in alerts:
            temp = alert.get("max_temp_c", 0)
            px = alert.get("blob_pixels", 0)
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] THERMAL {temp:.1f}°C ({px}px)\n"
            self._det_text.configure(state="normal")
            self._det_text.insert("end", line, "thermal")
            self._det_text.configure(state="disabled")
            self._det_text.see("end")

    def _on_disconnect(self) -> None:
        """Called when Bluetooth connection drops."""
        self.root.after(0, self._handle_disconnect)

    def _handle_disconnect(self) -> None:
        self._conn_var.set("● DISCONNECTED")
        self._bt_status_var.set("BT: LOST")
        self._log_alert({
            "level": "critical",
            "title": "DISCONNECTED",
            "message": "Bluetooth link lost — drone out of range or powered off",
            "datetime": datetime.now().strftime("%H:%M:%S"),
        })
        messagebox.showwarning(
            "Connection Lost",
            "Bluetooth link to AURA drone lost.\n\n"
            "The drone will return home automatically after 30 seconds\n"
            "if it does not regain contact."
        )

    # ──────────────────────────────────────────
    # Command Sending
    # ──────────────────────────────────────────

    def _send(self, cmd: str, params: dict = None) -> None:
        if not self.conn.is_connected:
            messagebox.showwarning("Not Connected", "No Bluetooth connection to drone.")
            return
        self.conn.send_command(cmd=cmd, params=params or {})
        self._log_alert({
            "level": "info",
            "title": "CMD SENT",
            "message": f"{cmd} {params or ''}",
            "datetime": datetime.now().strftime("%H:%M:%S"),
        })

    def _send_raw(self) -> None:
        text = self._cmd_entry.get().strip()
        if not text:
            return
        self._cmd_entry.delete(0, "end")
        if not self.conn.is_connected:
            messagebox.showwarning("Not Connected", "No Bluetooth connection to drone.")
            return
        self.conn.send_command(raw_text=text)
        self._log_alert({
            "level": "info",
            "title": "CMD SENT",
            "message": text,
            "datetime": datetime.now().strftime("%H:%M:%S"),
        })

    # ──────────────────────────────────────────
    # Clock / Ticker
    # ──────────────────────────────────────────

    def _tick(self) -> None:
        """Update connection status indicator."""
        if self.conn.is_connected:
            self._conn_var.set("● CONNECTED")
            self._bt_status_var.set("BT: LIVE")
        self.root.after(1000, self._tick)

    def _on_close(self) -> None:
        self.conn.disconnect()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
# CLI fallback (no GUI)
# ─────────────────────────────────────────────────────────────────────────────

def run_cli(conn: AURAConnection) -> None:
    """Minimal CLI ground station for headless use."""
    print("\nAURA Bluetooth Ground Station — CLI mode")
    print("Commands: takeoff <alt>, land, rth, hover, patrol, scout, sitrep, quit")
    print("Or type any natural language command.\n")

    def on_packet(p):
        ptype = p.get("type", "")
        if ptype == "telemetry":
            d = p.get("data", {})
            print(f"\r  BAT:{d.get('battery_percent','?')}%  "
                  f"ALT:{d.get('altitude_m',0):.1f}m  "
                  f"MODE:{d.get('mode','?')}  "
                  f"GPS:{d.get('gps_fix','?')}  "
                  f"ARMED:{'Y' if d.get('armed') else 'N'}  ",
                  end="", flush=True)
        elif ptype == "alert":
            a = p.get("data", {})
            level = a.get("level", "info").upper()
            print(f"\n[{level}] {a.get('title','')}: {a.get('message','')}")

    conn.on_packet = on_packet

    shorthand = {
        "land": "land",
        "rth": "return_home",
        "hover": "hover",
        "patrol": "patrol",
        "scout": "scout",
        "sitrep": "situation_report",
    }

    while conn.is_connected:
        try:
            text = input("\nCMD> ").strip()
            if not text:
                continue
            if text.lower() in ("quit", "exit"):
                break
            parts = text.split()
            cmd_word = parts[0].lower()
            if cmd_word == "takeoff":
                alt = float(parts[1]) if len(parts) > 1 else 30
                conn.send_command("takeoff", {"altitude": alt})
            elif cmd_word in shorthand:
                conn.send_command(shorthand[cmd_word])
            else:
                conn.send_command(raw_text=text)
        except (KeyboardInterrupt, EOFError):
            break

    conn.disconnect()
    print("\nDisconnected.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AURA Bluetooth Ground Station Client")
    parser.add_argument("--mac", help="Drone Bluetooth MAC address (skip scan)")
    parser.add_argument("--scan", action="store_true", help="Scan only, don't connect")
    parser.add_argument("--cli", action="store_true", help="CLI mode (no GUI)")
    parser.add_argument("--channel", type=int, default=1, help="RFCOMM channel (default 1)")
    args = parser.parse_args()

    conn = AURAConnection()

    # Scan only mode
    if args.scan:
        conn.scan()
        return

    # Find drone MAC
    mac = args.mac
    if not mac:
        devices = conn.scan()
        if not devices:
            print("\nNo AURA drones found. Make sure the drone is:")
            print("  1. Powered on and running drone_main.py")
            print("  2. Bluetooth is enabled (sudo systemctl start bluetooth)")
            print("  3. You have paired this device: bluetoothctl → pair <MAC>")
            print("\nOr specify MAC manually: python bt_client.py --mac AA:BB:CC:DD:EE:FF")
            sys.exit(1)

        if len(devices) == 1:
            mac = devices[0]["mac"]
            args.channel = devices[0].get("channel", 1)
        else:
            print("\nMultiple drones found:")
            for i, d in enumerate(devices):
                print(f"  {i+1}. {d['name']} ({d['mac']})")
            choice = int(input("Select drone (number): ")) - 1
            mac = devices[choice]["mac"]
            args.channel = devices[choice].get("channel", 1)

    # Connect
    if not conn.connect(mac, args.channel):
        print("Failed to connect. Exiting.")
        sys.exit(1)

    # Launch GUI or CLI
    if args.cli or not GUI_AVAILABLE:
        run_cli(conn)
    else:
        app = BTGroundStation(conn)
        app.run()


if __name__ == "__main__":
    main()
