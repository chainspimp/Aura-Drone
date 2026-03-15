"""
ui/ground_station.py — AURA Ground Station GUI

Tkinter-based ground control interface. Displays:
- Live main camera feed with YOLO bounding box overlays
- False-color thermal camera feed (side by side)
- Real-time telemetry panel (battery, altitude, GPS, heading, speed, mode)
- Scrolling alert log with color-coded severity levels
- Mini-map showing drone position + detection markers
- Control buttons (takeoff, land, RTH, patrol, scout, hover)
- Text command input box

The GUI runs in the main thread (Tkinter requirement).
All heavy work (camera, AI, MAVLink) runs in background threads that
write to shared state; the GUI's update loop reads from that state.
"""

import logging
import math
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk, scrolledtext, messagebox
from typing import Callable, Optional

import config

logger = logging.getLogger("AURA.gui")

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("Pillow not installed — camera feeds will be unavailable in GUI")

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


class GroundStation:
    """
    Full-featured ground control station GUI.

    Layout (1280 x 800 default):
    ┌────────────────────────────────────────────────────────────────┐
    │  ┌──────────────┐  ┌─────────────┐  ┌────────────────────────┐│
    │  │  Main Camera  │  │   Thermal   │  │     Telemetry          ││
    │  │  (with YOLO)  │  │  (IR view)  │  │   Battery / Alt / GPS  ││
    │  └──────────────┘  └─────────────┘  └────────────────────────┘│
    │  ┌──────────────────────────────────┐  ┌──────────────────────┐│
    │  │        Alert Log                  │  │      Mini Map        ││
    │  │  (scrolling, color-coded)         │  │  (grid + markers)    ││
    │  └──────────────────────────────────┘  └──────────────────────┘│
    │  ┌──────────────────────────────────────────────────────────── ┐│
    │  │ [Takeoff] [Land] [RTH] [Hover] [Patrol] [Scout] [Command:] ││
    │  └────────────────────────────────────────────────────────────┘│
    └────────────────────────────────────────────────────────────────┘
    """

    # GUI color scheme — dark tactical theme
    BG_DARK = "#1a1a2e"
    BG_PANEL = "#16213e"
    BG_ACCENT = "#0f3460"
    FG_TEXT = "#e0e0e0"
    FG_DIM = "#888888"
    COLOR_GREEN = "#00ff7f"
    COLOR_RED = "#ff4444"
    COLOR_ORANGE = "#ff8c00"
    COLOR_YELLOW = "#ffd700"
    COLOR_BLUE = "#4fc3f7"
    FONT_MONO = ("Courier", 10)
    FONT_LABEL = ("Helvetica", 9, "bold")
    FONT_VALUE = ("Courier", 11, "bold")
    FONT_TITLE = ("Helvetica", 12, "bold")

    def __init__(
        self,
        drone=None,
        yolo=None,
        thermal=None,
        alerts=None,
        on_command: Callable = None,
        shutdown_event: threading.Event = None,
    ) -> None:
        self.drone = drone
        self.yolo = yolo
        self.thermal = thermal
        self.alerts = alerts
        self.on_command = on_command
        self.shutdown_event = shutdown_event
        self._command_processor: Optional[Callable] = None

        # State
        self._drone_lat: float = 0.0
        self._drone_lon: float = 0.0
        self._home_lat: float = 0.0
        self._home_lon: float = 0.0
        self._detection_markers: list[dict] = []
        self._last_alert_count: int = 0

        # Tkinter root
        self.root: Optional[tk.Tk] = None

        # Camera image references (kept to prevent GC)
        self._main_photo = None
        self._thermal_photo = None

    def set_command_processor(self, processor: Callable) -> None:
        """Register the main loop's command processor for periodic calls."""
        self._command_processor = processor

    # ──────────────────────────────────────────
    # Build UI
    # ──────────────────────────────────────────

    def run(self) -> None:
        """Build and run the Tkinter GUI (blocking — must be called from main thread)."""
        self.root = tk.Tk()
        self.root.title("AURA Drone — Ground Station")
        self.root.configure(bg=self.BG_DARK)
        self.root.geometry("1280x800")
        self.root.minsize(960, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_layout()
        self._schedule_updates()

        logger.info("Ground station GUI started")
        self.root.mainloop()

    def _build_layout(self) -> None:
        """Construct all GUI panels."""
        self.root.columnconfigure(0, weight=2)
        self.root.columnconfigure(1, weight=2)
        self.root.columnconfigure(2, weight=1)
        self.root.rowconfigure(0, weight=2)
        self.root.rowconfigure(1, weight=2)
        self.root.rowconfigure(2, weight=0)

        self._build_main_feed()
        self._build_thermal_feed()
        self._build_telemetry_panel()
        self._build_alert_log()
        self._build_mini_map()
        self._build_control_bar()

    def _panel(self, parent, row, col, title: str, rowspan=1, columnspan=1) -> tk.Frame:
        """Helper to create a labeled panel frame."""
        outer = tk.Frame(parent, bg=self.BG_PANEL, bd=1, relief="solid")
        outer.grid(
            row=row, column=col, padx=4, pady=4,
            sticky="nsew", rowspan=rowspan, columnspan=columnspan
        )
        tk.Label(
            outer, text=f"▸ {title}", bg=self.BG_PANEL, fg=self.COLOR_BLUE,
            font=self.FONT_LABEL
        ).pack(anchor="w", padx=6, pady=(4, 0))
        inner = tk.Frame(outer, bg=self.BG_PANEL)
        inner.pack(fill="both", expand=True, padx=4, pady=4)
        return inner

    # ── Camera Feeds ──────────────────────────

    def _build_main_feed(self) -> None:
        """Main camera feed panel with YOLO overlay."""
        panel = self._panel(self.root, row=0, col=0, title="MAIN CAMERA (YOLO)")
        self._main_canvas = tk.Canvas(
            panel, bg="#000000", width=config.UI_FEED_WIDTH, height=config.UI_FEED_HEIGHT
        )
        self._main_canvas.pack(fill="both", expand=True)
        self._main_canvas.create_text(
            320, 240, text="Camera initializing...",
            fill=self.FG_DIM, font=self.FONT_MONO
        )

    def _build_thermal_feed(self) -> None:
        """Thermal camera false-color feed panel."""
        panel = self._panel(self.root, row=0, col=1, title="THERMAL (FLIR)")
        self._thermal_canvas = tk.Canvas(
            panel, bg="#000000",
            width=config.THERMAL_LEPTON_WIDTH * 4,
            height=config.THERMAL_LEPTON_HEIGHT * 4
        )
        self._thermal_canvas.pack(fill="both", expand=True)
        self._thermal_canvas.create_text(
            320, 240, text="Thermal initializing...",
            fill=self.FG_DIM, font=self.FONT_MONO
        )

    # ── Telemetry Panel ───────────────────────

    def _build_telemetry_panel(self) -> None:
        """Flight data telemetry panel."""
        panel = self._panel(self.root, row=0, col=2, title="TELEMETRY", rowspan=1)

        def add_row(parent, label: str):
            frame = tk.Frame(parent, bg=self.BG_PANEL)
            frame.pack(fill="x", pady=2)
            tk.Label(frame, text=label, bg=self.BG_PANEL, fg=self.FG_DIM,
                     font=self.FONT_LABEL, width=12, anchor="w").pack(side="left")
            var = tk.StringVar(value="---")
            tk.Label(frame, textvariable=var, bg=self.BG_PANEL, fg=self.COLOR_GREEN,
                     font=self.FONT_VALUE, anchor="w").pack(side="left")
            return var

        self._tel_battery = add_row(panel, "BATTERY")
        self._tel_altitude = add_row(panel, "ALTITUDE")
        self._tel_lat = add_row(panel, "LATITUDE")
        self._tel_lon = add_row(panel, "LONGITUDE")
        self._tel_heading = add_row(panel, "HEADING")
        self._tel_speed = add_row(panel, "SPEED")
        self._tel_mode = add_row(panel, "MODE")
        self._tel_gps = add_row(panel, "GPS FIX")
        self._tel_armed = add_row(panel, "ARMED")
        self._tel_time = add_row(panel, "FLIGHT TIME")

        self._flight_start_time: Optional[float] = None

    # ── Alert Log ─────────────────────────────

    def _build_alert_log(self) -> None:
        """Scrolling color-coded alert log."""
        panel = self._panel(self.root, row=1, col=0, title="ALERTS", columnspan=2)

        self._alert_text = scrolledtext.ScrolledText(
            panel, bg="#0d0d1a", fg=self.FG_TEXT,
            font=self.FONT_MONO, state="disabled",
            height=10, wrap="word",
        )
        self._alert_text.pack(fill="both", expand=True)

        # Color tags for severity
        self._alert_text.tag_config("critical", foreground=self.COLOR_RED, font=("Courier", 10, "bold"))
        self._alert_text.tag_config("error", foreground=self.COLOR_ORANGE)
        self._alert_text.tag_config("warning", foreground=self.COLOR_YELLOW)
        self._alert_text.tag_config("info", foreground=self.FG_TEXT)
        self._alert_text.tag_config("debug", foreground=self.FG_DIM)

    # ── Mini Map ──────────────────────────────

    def _build_mini_map(self) -> None:
        """Simple grid-based position map."""
        panel = self._panel(self.root, row=1, col=2, title="MAP")
        size = config.UI_MAP_GRID_SIZE
        self._map_canvas = tk.Canvas(
            panel, bg="#0d1a0d", width=size, height=size
        )
        self._map_canvas.pack(fill="both", expand=True)

        # Draw grid lines
        grid_step = size // 10
        for i in range(0, size + 1, grid_step):
            self._map_canvas.create_line(i, 0, i, size, fill="#1a2a1a", width=1)
            self._map_canvas.create_line(0, i, size, i, fill="#1a2a1a", width=1)

        # Center crosshair (home position)
        cx, cy = size // 2, size // 2
        self._map_canvas.create_line(cx - 10, cy, cx + 10, cy, fill=self.COLOR_GREEN, width=2)
        self._map_canvas.create_line(cx, cy - 10, cx, cy + 10, fill=self.COLOR_GREEN, width=2)
        self._map_canvas.create_text(cx + 8, cy - 10, text="HOME", fill=self.COLOR_GREEN,
                                      font=("Courier", 8))

        # Drone marker (will be moved in update)
        self._drone_marker = self._map_canvas.create_polygon(
            cx, cy - 8, cx + 6, cy + 6, cx - 6, cy + 6,
            fill=self.COLOR_BLUE, outline="white", width=1
        )

    # ── Control Bar ───────────────────────────

    def _build_control_bar(self) -> None:
        """Flight control buttons + command input."""
        bar = tk.Frame(self.root, bg=self.BG_ACCENT, pady=6)
        bar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 4))

        btn_style = {
            "bg": self.BG_DARK,
            "fg": self.FG_TEXT,
            "font": self.FONT_LABEL,
            "relief": "raised",
            "padx": 10, "pady": 6,
            "cursor": "hand2",
        }

        # Flight control buttons
        buttons = [
            ("TAKEOFF", self._cmd_takeoff, self.COLOR_GREEN),
            ("LAND", self._cmd_land, self.COLOR_ORANGE),
            ("RTH", self._cmd_rth, self.COLOR_YELLOW),
            ("HOVER", self._cmd_hover, self.COLOR_BLUE),
            ("PATROL", self._cmd_patrol, "#bb86fc"),
            ("SCOUT", self._cmd_scout, "#cf6679"),
            ("SITREP", self._cmd_sitrep, "#80cbc4"),
        ]

        for label, cmd, color in buttons:
            btn = tk.Button(bar, text=label, command=cmd, **btn_style)
            btn["fg"] = color
            btn.pack(side="left", padx=3)

        # Spacer
        tk.Frame(bar, bg=self.BG_ACCENT, width=20).pack(side="left")

        # Command input
        tk.Label(bar, text="CMD:", bg=self.BG_ACCENT, fg=self.FG_TEXT,
                 font=self.FONT_LABEL).pack(side="left")
        self._cmd_entry = tk.Entry(bar, bg="#0d0d1a", fg=self.COLOR_GREEN,
                                    font=self.FONT_MONO, width=40,
                                    insertbackground=self.COLOR_GREEN)
        self._cmd_entry.pack(side="left", padx=4)
        self._cmd_entry.bind("<Return>", self._on_command_enter)

        tk.Button(bar, text="SEND", command=self._on_command_send,
                  **btn_style).pack(side="left", padx=3)

        # Status indicator
        self._status_var = tk.StringVar(value="● ONLINE")
        tk.Label(bar, textvariable=self._status_var, bg=self.BG_ACCENT,
                 fg=self.COLOR_GREEN, font=self.FONT_LABEL).pack(side="right", padx=10)

    # ──────────────────────────────────────────
    # Update Loop
    # ──────────────────────────────────────────

    def _schedule_updates(self) -> None:
        """Schedule periodic GUI updates via Tkinter's after() mechanism."""
        self._update_all()

    def _update_all(self) -> None:
        """Called every UI_UPDATE_INTERVAL_MS — refreshes all panels from live data."""
        if not self.root:
            return

        try:
            self._update_camera_feed()
            self._update_thermal_feed()
            self._update_telemetry()
            self._update_alerts()
            self._update_minimap()

            # Process next queued command
            if self._command_processor:
                self._command_processor()

        except Exception as e:
            logger.error(f"GUI update error: {e}")

        # Schedule next update
        self.root.after(config.UI_UPDATE_INTERVAL_MS, self._update_all)

    def _update_camera_feed(self) -> None:
        """Pull latest annotated frame from YOLO watcher and display it."""
        if not self.yolo or not PIL_AVAILABLE or not CV2_AVAILABLE:
            return

        frame = self.yolo.get_annotated_frame()
        if frame is None:
            frame = self.yolo.get_current_frame()
        if frame is None:
            return

        try:
            # Resize to fit canvas
            canvas_w = self._main_canvas.winfo_width() or config.UI_FEED_WIDTH
            canvas_h = self._main_canvas.winfo_height() or config.UI_FEED_HEIGHT
            frame_resized = cv2.resize(frame, (canvas_w, canvas_h))

            # Convert BGR → RGB → PIL → ImageTk
            rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb)
            self._main_photo = ImageTk.PhotoImage(image=pil_image)

            self._main_canvas.delete("all")
            self._main_canvas.create_image(0, 0, anchor="nw", image=self._main_photo)

            # FPS overlay
            fps = self.yolo.get_fps()
            self._main_canvas.create_text(
                10, 10, anchor="nw",
                text=f"YOLO {fps:.1f} fps",
                fill=self.COLOR_GREEN, font=("Courier", 9)
            )
        except Exception as e:
            logger.debug(f"Camera feed update error: {e}")

    def _update_thermal_feed(self) -> None:
        """Pull thermal false-color frame and display it."""
        if not self.thermal or not PIL_AVAILABLE:
            return

        frame = self.thermal.get_visual_frame()
        if frame is None:
            return

        try:
            canvas_w = self._thermal_canvas.winfo_width() or (config.THERMAL_LEPTON_WIDTH * 4)
            canvas_h = self._thermal_canvas.winfo_height() or (config.THERMAL_LEPTON_HEIGHT * 4)
            frame_resized = cv2.resize(frame, (canvas_w, canvas_h)) if CV2_AVAILABLE else frame

            rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB) if CV2_AVAILABLE else frame_resized
            pil_image = Image.fromarray(rgb)
            self._thermal_photo = ImageTk.PhotoImage(image=pil_image)

            self._thermal_canvas.delete("all")
            self._thermal_canvas.create_image(0, 0, anchor="nw", image=self._thermal_photo)

            # Temperature scale overlay
            recent = self.thermal.get_recent_alerts(window_s=5.0) if self.thermal else []
            if recent:
                self._thermal_canvas.create_text(
                    10, 10, anchor="nw",
                    text=f"⚠ HEAT SIG {len(recent)}",
                    fill=self.COLOR_RED, font=("Courier", 10, "bold")
                )
        except Exception as e:
            logger.debug(f"Thermal feed update error: {e}")

    def _update_telemetry(self) -> None:
        """Update telemetry panel values from drone state."""
        if not self.drone:
            return

        try:
            tel = self.drone.get_telemetry()

            bat = tel.get("battery_percent", 0)
            bat_str = f"{bat}%"
            bat_color = (
                self.COLOR_RED if bat <= config.BATTERY_CRITICAL_PERCENT else
                self.COLOR_ORANGE if bat <= config.BATTERY_WARN_PERCENT else
                self.COLOR_GREEN
            )

            self._tel_battery.set(bat_str)
            self._tel_altitude.set(f"{tel.get('altitude_m', 0):.1f} m")
            self._tel_lat.set(f"{tel.get('latitude', 0):.5f}°")
            self._tel_lon.set(f"{tel.get('longitude', 0):.5f}°")
            self._tel_heading.set(f"{tel.get('heading_deg', 0):.0f}°")
            self._tel_speed.set(f"{tel.get('groundspeed_ms', 0):.1f} m/s")
            self._tel_mode.set(tel.get("mode", "?"))
            self._tel_gps.set(f"FIX {tel.get('gps_fix', 0)} ({tel.get('satellites', 0)} sats)")
            self._tel_armed.set("YES" if tel.get("armed") else "NO")

            # Flight time
            if tel.get("armed") and self._flight_start_time is None:
                self._flight_start_time = time.time()
            elif not tel.get("armed"):
                self._flight_start_time = None

            if self._flight_start_time:
                elapsed = int(time.time() - self._flight_start_time)
                self._tel_time.set(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
            else:
                self._tel_time.set("00:00")

            # Update drone position for minimap
            self._drone_lat = tel.get("latitude", 0)
            self._drone_lon = tel.get("longitude", 0)

        except Exception as e:
            logger.debug(f"Telemetry update error: {e}")

    def _update_alerts(self) -> None:
        """Append new alerts to the scrolling alert log."""
        if not self.alerts:
            return

        recent = self.alerts.get_recent(count=config.UI_MAX_ALERT_LOG_ENTRIES)
        current_count = len(recent)

        if current_count <= self._last_alert_count:
            return  # Nothing new

        # Add only new alerts
        new_alerts = recent[self._last_alert_count:]
        self._last_alert_count = current_count

        self._alert_text.configure(state="normal")
        for alert in new_alerts:
            line = alert.format_display() + "\n"
            self._alert_text.insert("end", line, alert.level)
        self._alert_text.configure(state="disabled")
        self._alert_text.see("end")  # Auto-scroll to bottom

    def _update_minimap(self) -> None:
        """Update drone marker position on the mini-map."""
        size = config.UI_MAP_GRID_SIZE
        cx, cy = size // 2, size // 2

        # For now: drone is always drawn near center
        # A real implementation would compute pixel offset from home GPS delta
        # using haversine-to-pixel scaling

        if self._drone_lat == 0 and self._drone_lon == 0:
            return

        # Compute offset from home in meters, scale to pixels
        if self._home_lat != 0:
            lat_diff = (self._drone_lat - self._home_lat) * 111320  # approx m/degree
            lon_diff = (self._drone_lon - self._home_lon) * (
                111320 * math.cos(math.radians(self._home_lat))
            )
            # Scale: 1 pixel = 2 meters
            px_x = int(cx + lon_diff / 2)
            px_y = int(cy - lat_diff / 2)  # Y inverted (north = up)
        else:
            self._home_lat = self._drone_lat
            self._home_lon = self._drone_lon
            px_x, px_y = cx, cy

        # Clamp to map bounds
        px_x = max(10, min(size - 10, px_x))
        px_y = max(10, min(size - 10, px_y))

        # Move drone marker triangle
        self._map_canvas.coords(
            self._drone_marker,
            px_x, px_y - 8,
            px_x + 6, px_y + 6,
            px_x - 6, px_y + 6,
        )

    # ──────────────────────────────────────────
    # Button Handlers
    # ──────────────────────────────────────────

    def _send_command(self, text: str) -> None:
        if self.on_command:
            self.on_command(text, source="gui")

    def _cmd_takeoff(self) -> None:
        self._send_command(f"takeoff {config.PATROL_ALTITUDE_M}")

    def _cmd_land(self) -> None:
        if messagebox.askyesno("Confirm Land", "Land the drone now?"):
            self._send_command("land")

    def _cmd_rth(self) -> None:
        if messagebox.askyesno("Confirm RTH", "Return to home?"):
            self._send_command("return home")

    def _cmd_hover(self) -> None:
        self._send_command("hover")

    def _cmd_patrol(self) -> None:
        self._send_command("patrol")

    def _cmd_scout(self) -> None:
        self._send_command("scout")

    def _cmd_sitrep(self) -> None:
        self._send_command("situation report")

    def _on_command_enter(self, event) -> None:
        self._on_command_send()

    def _on_command_send(self) -> None:
        text = self._cmd_entry.get().strip()
        if text:
            self._cmd_entry.delete(0, "end")
            self._send_command(text)

    # ──────────────────────────────────────────
    # Window Management
    # ──────────────────────────────────────────

    def _on_close(self) -> None:
        """Handle window close — confirm shutdown."""
        if messagebox.askyesno("Shutdown", "Shutdown AURA drone system?"):
            logger.info("GUI close requested")
            if self.shutdown_event:
                self.shutdown_event.set()
            self.root.destroy()
