"""
drone_main.py — AURA Drone System Entry Point

Bootstraps all subsystems, runs health checks, starts worker threads,
then enters the main command-dispatch loop. Handles graceful shutdown.

Usage:
    python drone_main.py [--no-gui] [--sim] [--demo]

    --no-gui    : Headless mode (no Tkinter window, CLI only)
    --sim       : Connect to SITL simulator instead of real Pixhawk
    --demo      : Run GUI with fully simulated data — no hardware required.
                  Fakes telemetry, camera feeds, detections, and alerts so
                  you can explore the interface on any PC.
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
from typing import Optional

# Ensure the project root is on sys.path when running directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from flight.drone_control import DroneController
from flight.emergency import EmergencyHandler
from vision.yolo_watch import YOLOWatcher
from vision.thermal_watch import ThermalWatcher
from vision.face_id import FaceIdentifier
from ai.command_parser import CommandParser
from ai.situation_report import SituationReporter
from ai.threat_assessor import ThreatAssessor
from comms.lora_bridge import LoRaBridge
from voice.speech_input import SpeechInput
from voice.tts_output import TTSOutput
from voice.wake_listener import WakeListener
from ui.alert_manager import AlertManager
from comms.bluetooth_bridge import BluetoothBridge
from comms.lora_telemetry import LoRaTelemetry

# ─────────────────────────────────────────────
# Logging Configuration
# ─────────────────────────────────────────────
os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
os.makedirs(config.DETECTION_LOG_DIR, exist_ok=True)
os.makedirs(config.SCOUT_REPORT_DIR, exist_ok=True)
os.makedirs(config.KNOWN_FACES_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("AURA.main")


class AURADrone:
    """
    Top-level orchestrator for the AURA Drone system.

    Owns all subsystem references and the main dispatch loop.
    Designed so that any subsystem failure degrades gracefully —
    a dead thermal camera should never kill flight operations.
    """

    def __init__(self, headless: bool = False, sim_mode: bool = False) -> None:
        self.headless = headless
        self.sim_mode = sim_mode
        self._shutdown_event = threading.Event()
        self._airborne = False

        # Subsystem references — populated during init_*() calls
        self.drone: Optional[DroneController] = None
        self.emergency: Optional[EmergencyHandler] = None
        self.yolo: Optional[YOLOWatcher] = None
        self.thermal: Optional[ThermalWatcher] = None
        self.face_id: Optional[FaceIdentifier] = None
        self.command_parser: Optional[CommandParser] = None
        self.situation_reporter: Optional[SituationReporter] = None
        self.threat_assessor: Optional[ThreatAssessor] = None
        self.lora: Optional[LoRaBridge] = None
        self.lora_telemetry: Optional[LoRaTelemetry] = None
        self.bluetooth: Optional[BluetoothBridge] = None
        self.speech_input: Optional[SpeechInput] = None
        self.tts: Optional[TTSOutput] = None
        self.wake_listener: Optional[WakeListener] = None
        self.alerts: Optional[AlertManager] = None
        self.gui = None  # Populated later if not headless

        # Command queue — voice/text commands feed into this
        self._command_queue: list[dict] = []
        self._command_lock = threading.Lock()

    # ──────────────────────────────────────────
    # Initialization Sequence
    # ──────────────────────────────────────────

    def init_all(self) -> bool:
        """
        Initialize every subsystem with health checks.
        Returns True if minimum viable systems are online (MAVLink + at least one camera).
        """
        logger.info("=" * 60)
        logger.info("  AURA Drone — Initializing Systems")
        logger.info("=" * 60)

        results = {}

        results["alerts"] = self._init_alerts()
        results["tts"] = self._init_tts()
        results["mavlink"] = self._init_mavlink()
        results["yolo"] = self._init_yolo()
        results["thermal"] = self._init_thermal()
        results["face_id"] = self._init_face_id()
        results["ai"] = self._init_ai()
        results["lora"] = self._init_lora()
        results["bluetooth"] = self._init_bluetooth()
        results["voice"] = self._init_voice()

        # Report results
        for system, ok in results.items():
            status = "✓ OK" if ok else "✗ FAILED"
            logger.info(f"  {system:20s} {status}")

        # Minimum viable: MAVLink must be connected, at least one camera active
        if not results["mavlink"]:
            logger.critical("MAVLink connection FAILED — cannot operate without flight controller")
            return False

        if not results["yolo"] and not results["thermal"]:
            logger.critical("All cameras FAILED — cannot operate blind")
            return False

        self.emergency = EmergencyHandler(drone=self.drone, alerts=self.alerts, tts=self.tts)
        self._start_battery_monitor()

        logger.info("AURA Drone systems initialized. Ready.")
        if self.tts:
            self.tts.speak("AURA online. All systems nominal. Awaiting commands.")
        return True

    def _init_alerts(self) -> bool:
        try:
            self.alerts = AlertManager()
            return True
        except Exception as e:
            logger.error(f"AlertManager init failed: {e}")
            return False

    def _init_tts(self) -> bool:
        try:
            self.tts = TTSOutput()
            return True
        except Exception as e:
            logger.warning(f"TTS init failed (audio alerts disabled): {e}")
            return False

    def _init_mavlink(self) -> bool:
        try:
            port = "tcp:127.0.0.1:5760" if self.sim_mode else config.MAVLINK_PORT
            baud = 115200 if self.sim_mode else config.MAVLINK_BAUD
            self.drone = DroneController()
            connected = self.drone.connect(port=port, baud=baud)
            if connected:
                telemetry = self.drone.get_telemetry()
                bat = telemetry.get("battery_percent", "?")
                alt = telemetry.get("altitude_m", 0)
                logger.info(f"MAVLink connected | Battery: {bat}% | Alt: {alt:.1f}m")
            return connected
        except Exception as e:
            logger.error(f"MAVLink init failed: {e}")
            return False

    def _init_yolo(self) -> bool:
        try:
            self.yolo = YOLOWatcher(camera_id=config.MAIN_CAMERA_ID)
            # Register callback so detections flow into our alert system
            self.yolo.register_callback(self._on_yolo_detection)
            self.yolo.start()
            return True
        except Exception as e:
            logger.error(f"YOLO watcher init failed: {e}")
            return False

    def _init_thermal(self) -> bool:
        try:
            self.thermal = ThermalWatcher(device_id=config.THERMAL_CAMERA_ID)
            self.thermal.register_callback(self._on_thermal_alert)
            self.thermal.start()
            return True
        except Exception as e:
            logger.warning(f"Thermal camera init failed (continuing without thermal): {e}")
            return False

    def _init_face_id(self) -> bool:
        try:
            self.face_id = FaceIdentifier(known_faces_dir=config.KNOWN_FACES_DIR)
            self.face_id.register_callback(self._on_face_alert)
            # Attach to YOLO's frame stream
            if self.yolo:
                self.yolo.register_frame_callback(self.face_id.process_frame)
            return True
        except Exception as e:
            logger.warning(f"Face ID init failed (continuing without face recognition): {e}")
            return False

    def _init_ai(self) -> bool:
        try:
            self.command_parser = CommandParser()
            self.situation_reporter = SituationReporter()
            self.threat_assessor = ThreatAssessor()
            # Quick connectivity check
            ok = self.command_parser.check_ollama_available()
            if not ok:
                logger.warning("Ollama not responding — AI will use rule-based fallback")
            return True  # AI module failure is non-fatal
        except Exception as e:
            logger.error(f"AI init failed: {e}")
            return False

    def _init_lora(self) -> bool:
        try:
            self.lora = LoRaBridge(port=config.LORA_PORT, baud=config.LORA_BAUD)
            connected = self.lora.connect()
            if connected:
                self.lora.start_receive_thread()
                # Start the telemetry broadcaster so the laptop gets live data
                self.lora_telemetry = LoRaTelemetry(
                    lora=self.lora,
                    drone=self.drone,
                    alerts=self.alerts,
                    yolo=self.yolo,
                    thermal=self.thermal,
                    on_command=self._enqueue_command,
                )
                self.lora_telemetry.start()
                logger.info(
                    "LoRa telemetry broadcaster started — "
                    "run lora_client.py on your laptop to connect"
                )
            return connected
        except Exception as e:
            logger.warning(f"LoRa init failed (comms relay disabled): {e}")
            return False

    def _init_bluetooth(self) -> bool:
        try:
            self.bluetooth = BluetoothBridge(
                drone=self.drone,
                alerts=self.alerts,
                yolo=self.yolo,
                thermal=self.thermal,
                on_command=self._enqueue_command,
            )
            started = self.bluetooth.start()
            if started:
                logger.info("Bluetooth bridge active — run bt_client.py on your laptop to connect")
            return started
        except Exception as e:
            logger.warning(f"Bluetooth bridge init failed (continuing without BT): {e}")
            return False

    def _init_voice(self) -> bool:
        try:
            self.speech_input = SpeechInput(model_path=config.VOSK_MODEL_PATH)
            self.speech_input.register_callback(self._on_voice_command)

            self.wake_listener = WakeListener(
                speech_input=self.speech_input,
                wake_word=config.WAKE_WORD,
                on_wake=self._on_wake_word,
            )
            self.wake_listener.start()
            return True
        except Exception as e:
            logger.warning(f"Voice input init failed (voice control disabled): {e}")
            return False

    # ──────────────────────────────────────────
    # Background Monitor Threads
    # ──────────────────────────────────────────

    def _start_battery_monitor(self) -> None:
        """
        Critical safety thread — runs independently of everything else.
        Even if the main loop hangs, this will trigger RTH or landing.
        """
        def _monitor():
            while not self._shutdown_event.is_set():
                try:
                    if self.drone:
                        telemetry = self.drone.get_telemetry()
                        bat_pct = telemetry.get("battery_percent", 100)
                        bat_v = telemetry.get("battery_voltage", 25.0)

                        if bat_pct <= config.BATTERY_CRITICAL_PERCENT or \
                                bat_v <= config.BATTERY_CRITICAL_VOLTAGE:
                            logger.critical(
                                f"CRITICAL BATTERY: {bat_pct}% / {bat_v:.1f}V — "
                                f"Initiating emergency landing"
                            )
                            self._dispatch_alert(
                                "CRITICAL BATTERY",
                                f"Battery at {bat_pct}% ({bat_v:.1f}V). Landing now.",
                                level="critical"
                            )
                            if self.emergency:
                                self.emergency.handle_critical_battery()

                        elif bat_pct <= config.BATTERY_WARN_PERCENT:
                            logger.warning(f"Low battery: {bat_pct}% / {bat_v:.1f}V")
                            self._dispatch_alert(
                                "LOW BATTERY",
                                f"Battery at {bat_pct}%. Return home soon.",
                                level="warning"
                            )

                except Exception as e:
                    logger.error(f"Battery monitor error: {e}")

                time.sleep(config.BATTERY_MONITOR_INTERVAL_S)

        t = threading.Thread(target=_monitor, name="BatteryMonitor", daemon=True)
        t.start()
        logger.info("Battery monitor thread started")

    # ──────────────────────────────────────────
    # Event Callbacks (from subsystems → main)
    # ──────────────────────────────────────────

    def _on_yolo_detection(self, detection: dict) -> None:
        """Receive YOLO detection events from the vision thread."""
        label = detection.get("class", "unknown")
        confidence = detection.get("confidence", 0.0)
        gps = self._get_current_gps()

        # Enrich detection with current GPS position
        detection["gps"] = gps
        detection["timestamp"] = time.time()

        if label in config.YOLO_THREAT_CLASSES:
            threat_msg = f"{label.upper()} detected ({confidence:.0%} confidence)"
            logger.warning(f"THREAT DETECTION: {threat_msg} @ {gps}")
            self._dispatch_alert("THREAT DETECTED", threat_msg, level="warning")

            # Ask threat assessor if this warrants immediate operator action
            if self.threat_assessor:
                assessment = self.threat_assessor.assess(detection)
                if assessment.get("urgent"):
                    spoken = f"Warning. {label} detected. {assessment.get('recommendation', '')}"
                    if self.tts:
                        self.tts.speak(spoken)
        else:
            logger.info(f"Detection: {label} ({confidence:.0%}) @ {gps}")

    def _on_thermal_alert(self, alert: dict) -> None:
        """Receive human-temperature blob alerts from the thermal thread."""
        gps = self._get_current_gps()
        alert["gps"] = gps
        msg = (
            f"Human heat signature detected. "
            f"Temp: {alert.get('max_temp_c', '?'):.1f}°C, "
            f"Blob size: {alert.get('blob_pixels', '?')} px"
        )
        logger.warning(f"THERMAL ALERT: {msg} @ {gps}")
        self._dispatch_alert("THERMAL ALERT", msg, level="warning")
        if self.tts:
            self.tts.speak("Thermal alert. Possible human contact detected.")

    def _on_face_alert(self, alert: dict) -> None:
        """Receive face recognition alerts — unknown faces trigger operator alert."""
        gps = self._get_current_gps()
        name = alert.get("name", "Unknown")
        if name == "Unknown":
            msg = f"Unidentified individual detected at {gps}"
            logger.warning(f"FACE ALERT: {msg}")
            self._dispatch_alert("UNKNOWN PERSON", msg, level="warning")
            if self.tts:
                self.tts.speak("Alert. Unknown individual detected in frame.")
        else:
            logger.info(f"Known person confirmed: {name}")

    def _on_wake_word(self) -> None:
        """Wake word 'Hey AURA' detected — activate full voice listening."""
        logger.info("Wake word detected")
        if self.tts:
            self.tts.speak("Yes?")
        if self.speech_input:
            self.speech_input.set_active(True)

    def _on_voice_command(self, command_text: str) -> None:
        """Receive transcribed voice command from speech thread."""
        logger.info(f"Voice command received: '{command_text}'")
        self._enqueue_command(command_text, source="voice")

    # ──────────────────────────────────────────
    # Command Processing
    # ──────────────────────────────────────────

    def _enqueue_command(self, raw_text: str, source: str = "text") -> None:
        """Thread-safe command enqueue."""
        with self._command_lock:
            self._command_queue.append({"text": raw_text, "source": source})

    def _process_next_command(self) -> None:
        """Parse and dispatch the next command from the queue."""
        with self._command_lock:
            if not self._command_queue:
                return
            cmd_entry = self._command_queue.pop(0)

        raw = cmd_entry["text"]
        source = cmd_entry["source"]
        logger.info(f"Processing command [{source}]: '{raw}'")

        try:
            if self.command_parser:
                parsed = self.command_parser.parse(raw)
            else:
                # Fallback: simple keyword matching
                parsed = self._simple_parse(raw)

            self._dispatch_command(parsed)

        except Exception as e:
            logger.error(f"Command processing error: {e}")
            self._dispatch_alert("COMMAND ERROR", f"Failed to process: '{raw}'", level="error")

    def _simple_parse(self, text: str) -> dict:
        """
        Rule-based command parser — used when Ollama is unavailable.
        Covers the most critical flight commands so the drone is always controllable.
        """
        text_lower = text.lower().strip()
        if any(w in text_lower for w in ["return home", "rth", "go home"]):
            return {"action": "return_home"}
        if "hover" in text_lower or "hold" in text_lower:
            return {"action": "hover"}
        if "land" in text_lower:
            return {"action": "land"}
        if "takeoff" in text_lower or "take off" in text_lower:
            # Try to extract altitude
            import re
            m = re.search(r"(\d+)\s*(?:m|meter|meters)?", text_lower)
            alt = float(m.group(1)) if m else config.PATROL_ALTITUDE_M
            return {"action": "takeoff", "params": {"altitude": alt}}
        if "patrol" in text_lower:
            return {"action": "patrol"}
        if "scout" in text_lower:
            return {"action": "scout", "params": {"direction": text_lower}}
        if "drop" in text_lower or "payload" in text_lower:
            return {"action": "drop_payload"}
        if "situation" in text_lower or "report" in text_lower or "sitrep" in text_lower:
            return {"action": "situation_report"}
        if "what" in text_lower and ("see" in text_lower or "detect" in text_lower):
            return {"action": "what_do_you_see"}
        return {"action": "unknown", "raw": text}

    def _dispatch_command(self, parsed: dict) -> None:
        """Execute a parsed command dict against the appropriate subsystem."""
        action = parsed.get("action", "unknown")
        params = parsed.get("params", {})
        logger.info(f"Dispatching action: {action} params: {params}")

        # ── Flight commands ──
        if action == "takeoff":
            alt = params.get("altitude", config.PATROL_ALTITUDE_M)
            if self.drone:
                self.drone.takeoff(altitude_m=float(alt))
                self._airborne = True
                self._dispatch_alert("TAKEOFF", f"Ascending to {alt}m", level="info")

        elif action == "land":
            if self.drone:
                self.drone.land()
                self._airborne = False
                self._dispatch_alert("LANDING", "Switching to LAND mode", level="info")

        elif action == "return_home":
            if self.drone:
                self.drone.return_home()
                self._dispatch_alert("RTH", "Returning to home position", level="info")

        elif action == "hover":
            if self.drone:
                self.drone.hover()
                self._dispatch_alert("HOVER", "Holding position", level="info")

        elif action == "fly_to":
            lat = params.get("lat")
            lon = params.get("lon")
            alt = params.get("alt", config.PATROL_ALTITUDE_M)
            if self.drone and lat and lon:
                self.drone.fly_to(lat=float(lat), lon=float(lon), alt=float(alt))

        elif action == "orbit":
            lat = params.get("lat")
            lon = params.get("lon")
            radius = params.get("radius", config.ORBIT_DEFAULT_RADIUS_M)
            if self.drone and lat and lon:
                self.drone.orbit(
                    lat=float(lat), lon=float(lon),
                    radius_m=float(radius),
                    speed_ms=config.ORBIT_DEFAULT_SPEED_MS,
                    duration_s=params.get("duration", 60)
                )

        elif action == "patrol":
            from flight.perimeter_patrol import PerimeterPatrol
            waypoints = params.get("waypoints", [])
            if not waypoints:
                self._dispatch_alert("PATROL", "No waypoints defined — set waypoints first", level="warning")
                return
            patrol = PerimeterPatrol(drone=self.drone, yolo=self.yolo, thermal=self.thermal)
            t = threading.Thread(target=patrol.run, args=(waypoints,), daemon=True)
            t.start()

        elif action == "scout":
            from flight.route_scout import RouteScout
            start = params.get("start")
            end = params.get("end")
            if not start or not end:
                self._dispatch_alert("SCOUT", "Need start and end GPS coordinates", level="warning")
                return
            scout = RouteScout(drone=self.drone, yolo=self.yolo, thermal=self.thermal)
            t = threading.Thread(
                target=scout.run,
                args=(start, end, params.get("corridor_width_m", 50)),
                daemon=True
            )
            t.start()

        elif action == "drop_payload":
            from flight.payload_release import PayloadRelease
            payload = PayloadRelease(drone=self.drone)
            t = threading.Thread(target=payload.release_with_clearance_check, daemon=True)
            t.start()

        elif action == "situation_report":
            self._generate_situation_report()

        elif action == "what_do_you_see":
            self._describe_current_view()

        elif action == "set_relay":
            from comms.wifi_relay import WiFiRelay
            relay = WiFiRelay(drone=self.drone)
            t = threading.Thread(
                target=relay.deploy,
                args=(config.RELAY_ALTITUDE_M,),
                daemon=True
            )
            t.start()

        elif action == "send_lora_message":
            if self.lora:
                recipient = params.get("recipient", 0)
                msg = params.get("message", "")
                self.lora.send_message(recipient_id=recipient, message=msg)

        elif action == "unknown":
            logger.warning(f"Unknown command: {parsed.get('raw', '')}")
            self._dispatch_alert(
                "UNKNOWN COMMAND",
                f"Could not parse: '{parsed.get('raw', '')}'",
                level="warning"
            )
        else:
            logger.warning(f"Unhandled action: {action}")

    # ──────────────────────────────────────────
    # AI-Driven Operations
    # ──────────────────────────────────────────

    def _generate_situation_report(self) -> None:
        """Compile current sensor state into a human-readable SITREP via Ollama."""
        if not self.situation_reporter:
            self._dispatch_alert("SITREP", "AI not available", level="warning")
            return

        telemetry = self.drone.get_telemetry() if self.drone else {}
        detections = self.yolo.get_recent_detections() if self.yolo else []
        thermal_alerts = self.thermal.get_recent_alerts() if self.thermal else []

        report = self.situation_reporter.generate(
            telemetry=telemetry,
            detections=detections,
            thermal_alerts=thermal_alerts,
        )
        logger.info(f"SITREP: {report}")
        self._dispatch_alert("SITUATION REPORT", report, level="info")
        if self.tts:
            self.tts.speak(report[:500])
        # Forward full report text to laptop over LoRa
        if self.lora_telemetry:
            self.lora_telemetry.send_report(report)

    def _describe_current_view(self) -> None:
        """Capture a frame and ask the vision AI to describe what it sees."""
        if not self.yolo:
            self._dispatch_alert("VIEW", "Camera not available", level="warning")
            return

        frame = self.yolo.get_current_frame()
        if frame is None:
            self._dispatch_alert("VIEW", "No frame available", level="warning")
            return

        from ai.situation_report import SituationReporter
        desc = self.situation_reporter.describe_frame(frame) if self.situation_reporter else \
            "Visual AI not available"

        self._dispatch_alert("VISUAL REPORT", desc, level="info")
        if self.tts:
            self.tts.speak(desc[:400])

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    def _get_current_gps(self) -> dict:
        """Return current GPS coordinates from flight controller."""
        if self.drone:
            tel = self.drone.get_telemetry()
            return {
                "lat": tel.get("latitude", 0.0),
                "lon": tel.get("longitude", 0.0),
                "alt": tel.get("altitude_m", 0.0),
            }
        return {"lat": 0.0, "lon": 0.0, "alt": 0.0}

    def _dispatch_alert(self, title: str, message: str, level: str = "info") -> None:
        """Route an alert to AlertManager (and GUI if active)."""
        if self.alerts:
            self.alerts.add_alert(title=title, message=message, level=level)

    # ──────────────────────────────────────────
    # Main Loop & GUI
    # ──────────────────────────────────────────

    def run_headless(self) -> None:
        """
        Headless main loop for server/embedded use.
        Reads text commands from stdin in addition to voice.
        """
        logger.info("Running in headless mode. Type commands or speak them.")
        import select

        while not self._shutdown_event.is_set():
            # Process any queued commands
            self._process_next_command()

            # Non-blocking stdin read (Linux-compatible)
            if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline().strip()
                if line:
                    if line.lower() in ("quit", "exit", "shutdown"):
                        self.shutdown()
                        break
                    self._enqueue_command(line, source="text")

            time.sleep(0.05)

    def run_gui(self) -> None:
        """Launch Tkinter ground station GUI — must run in main thread."""
        from ui.ground_station import GroundStation
        self.gui = GroundStation(
            drone=self.drone,
            yolo=self.yolo,
            thermal=self.thermal,
            alerts=self.alerts,
            on_command=self._enqueue_command,
            shutdown_event=self._shutdown_event,
        )
        # GUI's mainloop also drives command processing via periodic callback
        self.gui.set_command_processor(self._process_next_command)
        self.gui.run()

    def shutdown(self) -> None:
        """
        Graceful shutdown sequence:
        1. If airborne: land (or RTH if far from home)
        2. Stop all threads
        3. Flush logs
        """
        logger.info("AURA shutdown initiated")

        if self.tts:
            self.tts.speak("Shutting down. Stay safe.")

        # Land if airborne
        if self._airborne and self.drone:
            logger.info("Drone is airborne — initiating landing")
            try:
                telemetry = self.drone.get_telemetry()
                # If more than 50m from home, use RTL; otherwise land in place
                dist_home = telemetry.get("distance_to_home_m", 0)
                if dist_home > 50:
                    self.drone.return_home()
                else:
                    self.drone.land()
                time.sleep(10)  # Give it time to start descending
            except Exception as e:
                logger.error(f"Shutdown landing failed: {e}")

        # Signal all threads to stop
        self._shutdown_event.set()

        # Stop subsystems
        for subsystem in [self.yolo, self.thermal, self.speech_input, self.wake_listener, self.lora, self.bluetooth]:
            if subsystem and hasattr(subsystem, "stop"):
                try:
                    subsystem.stop()
                except Exception as e:
                    logger.error(f"Error stopping {subsystem}: {e}")

        if self.drone:
            try:
                self.drone.close()
            except Exception:
                pass

        logger.info("AURA shutdown complete")


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AURA Drone System")
    parser.add_argument("--no-gui", action="store_true", help="Run headless (no GUI)")
    parser.add_argument("--sim", action="store_true", help="Connect to SITL simulator")
    args = parser.parse_args()

    aura = AURADrone(headless=args.no_gui, sim_mode=args.sim)

    # Register SIGINT/SIGTERM for clean Ctrl-C shutdown
    def _signal_handler(sig, frame):
        logger.info(f"Signal {sig} received — shutting down")
        aura.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if not aura.init_all():
        logger.critical("System initialization failed. Aborting.")
        sys.exit(1)

    if args.no_gui:
        aura.run_headless()
    else:
        # GUI must run in main thread; voice/detection run in background threads
        aura.run_gui()


if __name__ == "__main__":
    main()
