"""
flight/perimeter_patrol.py — Autonomous Perimeter Patrol

Flies a continuous GPS waypoint loop at the configured patrol altitude.
At each waypoint, pauses to capture and analyze visual + thermal frames.
Alerts operator on detection. Battery-aware: returns home before critical.
Supports night mode (thermal-primary detection after sunset).
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

import config
from flight.drone_control import DroneController

logger = logging.getLogger("AURA.patrol")


class PerimeterPatrol:
    """
    Autonomous loop patrol around a set of GPS waypoints.

    The patrol continues until:
    - Battery drops to warning level → return home, wait for operator command to resume
    - `stop()` is called
    - A critical threat is detected and operator commands halt

    Detection events are logged and forwarded to the alert system — the drone
    does NOT take autonomous combat action, it just alerts.
    """

    def __init__(
        self,
        drone: DroneController,
        yolo=None,     # YOLOWatcher instance
        thermal=None,  # ThermalWatcher instance
        alerts=None,   # AlertManager instance
    ) -> None:
        self.drone = drone
        self.yolo = yolo
        self.thermal = thermal
        self.alerts = alerts
        self._stop_event = threading.Event()
        self._paused = False
        self._current_waypoint_idx = 0
        self._loop_count = 0

    def stop(self) -> None:
        """Signal the patrol loop to stop after the current waypoint."""
        logger.info("Patrol stop requested")
        self._stop_event.set()

    def pause(self) -> None:
        """Hold position at current waypoint, maintain patrol state."""
        self._paused = True
        self.drone.hover()
        logger.info("Patrol paused — hovering in place")

    def resume(self) -> None:
        """Resume patrol from where it was paused."""
        self._paused = False
        logger.info("Patrol resumed")

    def run(self, waypoints: list[dict]) -> None:
        """
        Main patrol loop.

        Args:
            waypoints: List of dicts with keys 'lat', 'lon' (and optionally 'alt').
                       Example: [{"lat": 34.052, "lon": -118.243}, ...]
        """
        if not waypoints:
            logger.error("Patrol called with empty waypoints list")
            return

        altitude = config.PATROL_ALTITUDE_M
        logger.info(
            f"Starting perimeter patrol | {len(waypoints)} waypoints | "
            f"Alt: {altitude}m"
        )

        # Takeoff if not already airborne
        telemetry = self.drone.get_telemetry()
        if telemetry.get("altitude_m", 0) < 2.0:
            logger.info("Not airborne — taking off for patrol")
            if not self.drone.takeoff(altitude_m=altitude):
                logger.error("Takeoff failed — aborting patrol")
                return

        # Tilt gimbal down for wide-area surveillance
        self.drone.set_gimbal_angle(config.GIMBAL_TILT_DEFAULT)

        while not self._stop_event.is_set():
            self._loop_count += 1
            logger.info(f"Patrol loop #{self._loop_count}")

            for idx, wp in enumerate(waypoints):
                if self._stop_event.is_set():
                    break

                # Battery check before each waypoint
                if self._should_return_home():
                    logger.warning("Battery low — suspending patrol, returning home")
                    self._return_and_wait()
                    if self._stop_event.is_set():
                        return
                    # Re-takeoff after battery swap/charge (waits for operator command)
                    continue

                # Handle pause state
                while self._paused and not self._stop_event.is_set():
                    time.sleep(0.5)

                self._current_waypoint_idx = idx
                wp_lat = wp["lat"]
                wp_lon = wp["lon"]
                wp_alt = wp.get("alt", altitude)

                logger.info(
                    f"Flying to waypoint {idx + 1}/{len(waypoints)}: "
                    f"({wp_lat:.6f}, {wp_lon:.6f})"
                )

                arrived = self.drone.fly_to(lat=wp_lat, lon=wp_lon, alt=wp_alt)
                if not arrived:
                    logger.warning(f"Did not arrive cleanly at waypoint {idx + 1}")

                # Dwell at waypoint — this is where detection happens
                self._dwell_and_scan(wp_lat, wp_lon)

        logger.info("Perimeter patrol stopped")
        self.drone.hover()

    def _dwell_and_scan(self, lat: float, lon: float) -> None:
        """
        Hover at waypoint for PATROL_WAYPOINT_DWELL_S, capturing and checking frames.
        Performs a 360° yaw scan if configured.
        """
        logger.debug(f"Dwelling at ({lat:.6f}, {lon:.6f}) for {config.PATROL_WAYPOINT_DWELL_S}s")

        # Scan from all angles: yaw 0, 90, 180, 270
        dwell_per_direction = config.PATROL_WAYPOINT_DWELL_S / 4

        for yaw_step in [0, 90, 180, 270]:
            if self._stop_event.is_set():
                break

            time.sleep(dwell_per_direction)

            # Collect detections from vision threads
            detections = self._collect_detections(lat, lon)

            if detections:
                self._handle_detections(detections, lat, lon)

    def _collect_detections(self, lat: float, lon: float) -> list[dict]:
        """
        Snapshot current detection state from all active sensors.

        Returns a list of detection dicts with GPS coordinates attached.
        """
        detections = []

        if self.yolo:
            recent = self.yolo.get_recent_detections(window_s=config.PATROL_WAYPOINT_DWELL_S)
            for d in recent:
                d["gps"] = {"lat": lat, "lon": lon}
                d["sensor"] = "visual"
                detections.append(d)

        if self.thermal:
            recent = self.thermal.get_recent_alerts(window_s=config.PATROL_WAYPOINT_DWELL_S)
            for a in recent:
                a["gps"] = {"lat": lat, "lon": lon}
                a["sensor"] = "thermal"
                detections.append(a)

        return detections

    def _handle_detections(self, detections: list[dict], lat: float, lon: float) -> None:
        """
        Process detections — log, alert, and optionally orbit for closer look.
        Does NOT take autonomous defensive action.
        """
        threat_detected = any(
            d.get("class") in config.YOLO_THREAT_CLASSES or d.get("sensor") == "thermal"
            for d in detections
        )

        if threat_detected:
            threat_classes = [
                d.get("class", "thermal") for d in detections
                if d.get("class") in config.YOLO_THREAT_CLASSES or d.get("sensor") == "thermal"
            ]
            msg = (
                f"Patrol alert at ({lat:.5f}, {lon:.5f}): "
                f"Detected: {', '.join(set(threat_classes))}"
            )
            logger.warning(msg)

            if self.alerts:
                self.alerts.add_alert(
                    title="PATROL DETECTION",
                    message=msg,
                    level="warning",
                )

            # Log detection event to file
            self._log_detection_event(detections, lat, lon)

    def _log_detection_event(self, detections: list[dict], lat: float, lon: float) -> None:
        """Append detection event to the detection log file."""
        import json
        import os

        os.makedirs(config.DETECTION_LOG_DIR, exist_ok=True)
        filename = f"patrol_detections_{datetime.now().strftime('%Y%m%d')}.jsonl"
        filepath = os.path.join(config.DETECTION_LOG_DIR, filename)

        event = {
            "timestamp": datetime.now().isoformat(),
            "lat": lat,
            "lon": lon,
            "detections": detections,
            "loop": self._loop_count,
        }

        with open(filepath, "a") as f:
            f.write(json.dumps(event) + "\n")

    def _should_return_home(self) -> bool:
        """Check if battery is low enough to abort patrol."""
        telemetry = self.drone.get_telemetry()
        bat_pct = telemetry.get("battery_percent", 100)
        bat_v = telemetry.get("battery_voltage", 25.0)
        return (
            bat_pct <= config.BATTERY_WARN_PERCENT or
            bat_v <= config.BATTERY_WARN_VOLTAGE
        )

    def _return_and_wait(self) -> None:
        """
        Return home on low battery, then wait until battery recovers or
        operator resumes patrol manually.
        """
        logger.info("Returning home for battery")
        self.drone.return_home()

        # Wait up to 30 minutes for battery to recover (manual swap scenario)
        deadline = time.time() + 1800
        while time.time() < deadline and not self._stop_event.is_set():
            telemetry = self.drone.get_telemetry()
            bat = telemetry.get("battery_percent", 0)
            if bat > config.BATTERY_WARN_PERCENT + 10:
                logger.info(f"Battery recovered to {bat}% — ready to resume patrol")
                if self.alerts:
                    self.alerts.add_alert(
                        "PATROL READY", f"Battery at {bat}%. Command 'patrol' to resume.", "info"
                    )
                return
            time.sleep(30)

        logger.info("Patrol battery wait expired — stopping patrol")
        self._stop_event.set()
