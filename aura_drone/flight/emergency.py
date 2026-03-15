"""
flight/emergency.py — Failsafe and Emergency Handlers

Handles all abnormal flight situations:
- Critical battery → forced landing
- GPS lost → loiter then land
- MAVLink comms loss → return home
- Operator-commanded emergency land

This module is intentionally conservative — when in doubt, it lands.
"""

import logging
import threading
import time

import config
from flight.drone_control import DroneController

logger = logging.getLogger("AURA.emergency")


class EmergencyHandler:
    """
    Failsafe controller — runs independently from mission logic.

    All handlers default to landing rather than risk flyaway or crash.
    The battery monitor in drone_main.py calls this module when thresholds trip.
    """

    def __init__(self, drone: DroneController, alerts=None, tts=None) -> None:
        self.drone = drone
        self.alerts = alerts
        self.tts = tts
        self._emergency_active = False
        self._comms_loss_timer: threading.Timer = None
        self._last_comms_time: float = time.time()

        # Register MAVLink comms-loss watchdog
        self._start_comms_watchdog()

    # ──────────────────────────────────────────
    # Critical Battery
    # ──────────────────────────────────────────

    def handle_critical_battery(self) -> None:
        """
        Force landing immediately.
        If altitude is very high, switch to RTL first to allow controlled descent.
        """
        if self._emergency_active:
            return  # Prevent re-entrant handling
        self._emergency_active = True

        logger.critical("EMERGENCY: Critical battery — initiating forced landing")
        self._speak("Critical battery. Landing immediately.")
        self._alert("EMERGENCY LAND", "Critical battery level — forcing landing", "critical")

        telemetry = self.drone.get_telemetry()
        altitude = telemetry.get("altitude_m", 0)

        if altitude > 20:
            # High altitude — use RTL for controlled descent, auto-lands at home
            logger.info("High altitude detected — using RTL for battery emergency")
            self.drone.return_home()
        else:
            # Low altitude — land in place immediately
            self.drone.land()

    # ──────────────────────────────────────────
    # GPS Loss
    # ──────────────────────────────────────────

    def handle_gps_loss(self) -> None:
        """
        GPS fix lost mid-flight.
        Switch to LOITER (velocity-based hold) then initiate landing if GPS
        doesn't recover within a grace period.
        """
        logger.warning("GPS fix lost — switching to LOITER")
        self._alert("GPS LOST", "GPS signal lost — switching to LOITER mode", "warning")
        self._speak("Warning. GPS lost. Holding position.")

        self.drone.hover()  # LOITER uses optical flow / barometer

        # Wait up to 30 seconds for GPS to recover
        for i in range(30):
            time.sleep(1)
            telemetry = self.drone.get_telemetry()
            if telemetry.get("gps_fix", 0) >= 3:
                logger.info("GPS recovered")
                self._alert("GPS RECOVERED", "GPS signal restored", "info")
                return

        # GPS didn't recover — land in place
        logger.warning("GPS did not recover — landing in place")
        self._alert("GPS LAND", "GPS still lost after 30s — landing in place", "warning")
        self._speak("GPS recovery failed. Landing in place.")
        self.drone.land()

    # ──────────────────────────────────────────
    # Communications Loss
    # ──────────────────────────────────────────

    def update_comms_heartbeat(self) -> None:
        """
        Call this from the main loop to signal the GCS is still connected.
        The watchdog triggers if this isn't called for COMMS_LOSS_TIMEOUT_S.
        """
        self._last_comms_time = time.time()

    def _start_comms_watchdog(self) -> None:
        """
        Monitor for ground station comms loss.
        ArduPilot has its own FS_GCS_ENABL failsafe, but this provides
        an application-level backup.
        """
        COMMS_LOSS_TIMEOUT_S = 30

        def _watchdog():
            while True:
                time.sleep(5)
                if time.time() - self._last_comms_time > COMMS_LOSS_TIMEOUT_S:
                    if not self._emergency_active:
                        logger.warning(
                            f"GCS comms loss detected ({COMMS_LOSS_TIMEOUT_S}s) — "
                            f"triggering RTH"
                        )
                        self.handle_comms_loss()

        t = threading.Thread(target=_watchdog, name="CommsWatchdog", daemon=True)
        t.start()

    def handle_comms_loss(self) -> None:
        """GCS communication lost — return home autonomously."""
        if self._emergency_active:
            return
        # Don't mark emergency_active for comms loss (recoverable)
        logger.warning("FAILSAFE: GCS comms lost — returning home")
        self._alert("COMMS LOSS", "Ground station link lost — RTH activated", "warning")
        self.drone.return_home()

    # ──────────────────────────────────────────
    # Manual Emergency Commands
    # ──────────────────────────────────────────

    def emergency_land_now(self) -> None:
        """Operator-commanded immediate landing — no checks."""
        logger.info("OPERATOR: Emergency land command")
        self._speak("Emergency landing now.")
        self._alert("EMERGENCY LAND", "Operator commanded emergency landing", "critical")
        self.drone.land()

    def emergency_stop_motors(self) -> None:
        """
        LAST RESORT: Kill motors immediately via DISARM.
        WARNING: This will cause the drone to fall from any altitude.
        Only use if drone is out of control and about to hit people/property.
        """
        logger.critical("EMERGENCY STOP: Killing motors — drone will fall")
        self._speak("Emergency motor cutoff.")
        self._alert(
            "MOTOR CUTOFF",
            "EMERGENCY MOTOR DISARM ACTIVATED — DRONE WILL FALL",
            "critical"
        )
        if self.drone._vehicle:
            try:
                self.drone._vehicle.armed = False
            except Exception as e:
                logger.error(f"Motor kill failed: {e}")

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    def _alert(self, title: str, message: str, level: str) -> None:
        if self.alerts:
            self.alerts.add_alert(title=title, message=message, level=level)

    def _speak(self, text: str) -> None:
        if self.tts:
            try:
                self.tts.speak(text)
            except Exception:
                pass
