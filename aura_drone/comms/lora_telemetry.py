"""
comms/lora_telemetry.py — Drone-Side LoRa Telemetry Broadcaster

Runs on the Jetson. Periodically pushes telemetry, alerts, and detections
to the laptop running lora_client.py using the structured message protocol.

Also listens for incoming commands from the laptop and dispatches them
through the same command pipeline as voice and GUI.

Message types sent to laptop:
    TEL  — telemetry snapshot every 5 seconds
    ALT  — alert (threat detection, system event)
    DET  — YOLO detection event
    THM  — thermal alert
    RPT  — situation report text
    ACK  — command acknowledgment
    HBT  — heartbeat every 30 seconds

Commands received from laptop:
    CMD|<natural language command text>
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional, Callable

import config

logger = logging.getLogger("AURA.lora_tel")

TELEMETRY_INTERVAL_S  = 5     # How often to push telemetry
HEARTBEAT_INTERVAL_S  = 30    # How often to send heartbeat
LAPTOP_ADDRESS        = 2     # lora_client.py uses address 2


class LoRaTelemetry:
    """
    Bridges the AURA drone's internal state to the LoRa radio link.

    Sits between:
        drone systems (telemetry, alerts, detections)
                     ↓
              LoRaBridge (raw send/recv)
                     ↓
            laptop lora_client.py

    Runs two threads:
    - Push thread: sends telemetry + heartbeats on schedule
    - Command thread: watches LoRa inbox and dispatches commands
    """

    def __init__(
        self,
        lora,           # LoRaBridge instance
        drone=None,
        alerts=None,
        yolo=None,
        thermal=None,
        on_command: Optional[Callable] = None,
    ) -> None:
        self.lora = lora
        self.drone = drone
        self.alerts = alerts
        self.yolo = yolo
        self.thermal = thermal
        self.on_command = on_command

        self._running = False
        self._push_thread: Optional[threading.Thread] = None
        self._cmd_thread: Optional[threading.Thread] = None
        self._uptime_start = time.time()

        # Track what we've already sent to avoid re-sending old alerts
        self._last_alert_idx = 0
        self._last_det_time = 0.0
        self._last_thermal_time = 0.0

    def start(self) -> None:
        """Start push and command threads."""
        self._running = True

        self._push_thread = threading.Thread(
            target=self._push_loop, name="LoRa_Push", daemon=True
        )
        self._push_thread.start()

        self._cmd_thread = threading.Thread(
            target=self._command_loop, name="LoRa_Cmd", daemon=True
        )
        self._cmd_thread.start()

        logger.info("LoRa telemetry broadcaster started → laptop address 2")

    def stop(self) -> None:
        self._running = False

    # ──────────────────────────────────────────
    # Push Loop
    # ──────────────────────────────────────────

    def _push_loop(self) -> None:
        """Periodically push telemetry and new events to the laptop."""
        last_tel  = 0.0
        last_hb   = 0.0

        while self._running:
            now = time.time()

            # Telemetry — every TELEMETRY_INTERVAL_S
            if now - last_tel >= TELEMETRY_INTERVAL_S:
                self._send_telemetry()
                last_tel = now

            # Heartbeat — every HEARTBEAT_INTERVAL_S
            if now - last_hb >= HEARTBEAT_INTERVAL_S:
                self._send_heartbeat()
                last_hb = now

            # New alerts
            self._push_new_alerts()

            # New YOLO detections (batch, max 3 per cycle to avoid flooding)
            self._push_new_detections()

            # New thermal alerts
            self._push_new_thermal()

            time.sleep(1.0)

    def _send_telemetry(self) -> None:
        """Send current telemetry snapshot."""
        if not self.drone:
            return
        try:
            t = self.drone.get_telemetry()
            msg = (
                f"TEL|battery={t.get('battery_percent',0)},"
                f"volt={t.get('battery_voltage',0):.1f},"
                f"alt={t.get('altitude_m',0):.1f},"
                f"lat={t.get('latitude',0):.5f},"
                f"lon={t.get('longitude',0):.5f},"
                f"hdg={t.get('heading_deg',0):.0f},"
                f"spd={t.get('groundspeed_ms',0):.1f},"
                f"mode={t.get('mode','?')},"
                f"armed={1 if t.get('armed') else 0},"
                f"gps={t.get('gps_fix',0)},"
                f"sat={t.get('satellites',0)}"
            )
            self._send(msg)
        except Exception as e:
            logger.debug(f"LoRa telemetry send error: {e}")

    def _send_heartbeat(self) -> None:
        uptime = int(time.time() - self._uptime_start)
        self._send(f"HBT|uptime={uptime}")

    def _push_new_alerts(self) -> None:
        """Push any alerts that haven't been sent yet."""
        if not self.alerts:
            return
        all_alerts = self.alerts.get_all()
        new = all_alerts[self._last_alert_idx:]
        if not new:
            return
        self._last_alert_idx = len(all_alerts)

        # Only send warning and above over LoRa (info would flood the link)
        for alert in new:
            if alert.level in ("warning", "error", "critical"):
                msg = (
                    f"ALT|level={alert.level},"
                    f"title={alert.title},"
                    f"msg={alert.message[:80]}"
                )
                self._send(msg)
                time.sleep(0.5)  # Stagger to avoid TX collision

    def _push_new_detections(self) -> None:
        """Push recent YOLO detections (threat classes only)."""
        if not self.yolo:
            return
        try:
            recent = self.yolo.get_recent_detections(window_s=2.0)
            # Only send detections we haven't sent yet
            new = [d for d in recent if d.get("timestamp", 0) > self._last_det_time]
            if not new:
                return

            threat_dets = [d for d in new
                           if d.get("class") in config.YOLO_ALERT_CLASSES][:3]

            for det in threat_dets:
                gps = det.get("gps", {})
                cls  = det.get("class", "?")
                conf = f"{det.get('confidence', 0):.0%}"
                lat  = gps.get("lat", "")
                lon  = gps.get("lon", "")
                msg  = f"DET|class={cls},conf={conf},lat={lat},lon={lon}"
                self._send(msg)
                time.sleep(0.3)

            if new:
                self._last_det_time = max(d.get("timestamp", 0) for d in new)

        except Exception as e:
            logger.debug(f"LoRa detection push error: {e}")

    def _push_new_thermal(self) -> None:
        """Push recent thermal alerts."""
        if not self.thermal:
            return
        try:
            recent = self.thermal.get_recent_alerts(window_s=2.0)
            new = [a for a in recent if a.get("timestamp", 0) > self._last_thermal_time]
            if not new:
                return

            for alert in new[:2]:  # Max 2 per cycle
                temp = alert.get("max_temp_c", 0)
                px   = alert.get("blob_pixels", 0)
                gps  = alert.get("gps", {})
                msg  = (f"THM|temp={temp:.1f},px={px},"
                        f"lat={gps.get('lat','')},"
                        f"lon={gps.get('lon','')}")
                self._send(msg)
                time.sleep(0.3)

            if new:
                self._last_thermal_time = max(a.get("timestamp", 0) for a in new)

        except Exception as e:
            logger.debug(f"LoRa thermal push error: {e}")

    def send_report(self, report_text: str) -> None:
        """Send a situation report to the laptop (call from drone_main.py)."""
        # Truncate to fit LoRa payload — multiple packets if needed
        chunk_size = 220
        chunks = [report_text[i:i+chunk_size]
                  for i in range(0, min(len(report_text), 660), chunk_size)]
        for chunk in chunks:
            self._send(f"RPT|{chunk}")
            time.sleep(1.0)

    # ──────────────────────────────────────────
    # Command Loop
    # ──────────────────────────────────────────

    def _command_loop(self) -> None:
        """
        Poll the LoRa receive queue for commands from the laptop.
        Commands arrive as: CMD|<natural language text>
        """
        while self._running:
            try:
                messages = self.lora.get_messages()
                for msg in messages:
                    text = msg.get("message", "")
                    if text.startswith("CMD|"):
                        cmd_text = text[4:].strip()
                        logger.info(f"LoRa command from laptop: '{cmd_text}'")

                        # Acknowledge receipt
                        self._send(f"ACK|cmd={cmd_text[:30]}")

                        # Dispatch to main command handler
                        if self.on_command and cmd_text:
                            self.on_command(cmd_text, source="lora")

            except Exception as e:
                logger.debug(f"LoRa command loop error: {e}")

            time.sleep(0.5)

    # ──────────────────────────────────────────
    # Send Helper
    # ──────────────────────────────────────────

    def _send(self, message: str) -> None:
        """Send a message to the laptop (address 2)."""
        try:
            self.lora.send_message(
                recipient_id=LAPTOP_ADDRESS,
                message=message
            )
        except Exception as e:
            logger.debug(f"LoRa send error: {e}")
