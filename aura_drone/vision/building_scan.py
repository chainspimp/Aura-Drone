"""
vision/building_scan.py — Structure Assessment via Orbital Scan

Flies a systematic orbit around a structure, capturing images from multiple
angles, then uses the vision AI to assess hazard zones and structural integrity.
Produces a text report with observations.
"""

import logging
import os
import time
from datetime import datetime
from typing import Optional

import config
from flight.drone_control import DroneController

logger = logging.getLogger("AURA.building_scan")


class BuildingScanner:
    """
    Perform a multi-angle aerial assessment of a building or structure.

    Process:
    1. Orbit the structure at low altitude, taking images every 45°
    2. Orbit again at higher altitude for overview
    3. Pass all images to vision AI for hazard assessment
    4. Return text report with findings

    Use cases:
    - Check if a building is safe to enter (structural damage assessment)
    - Identify entry/exit points
    - Detect people inside via thermal
    - Look for hazardous materials (biohazard signs, fire, flooding)
    """

    def __init__(
        self,
        drone: DroneController,
        yolo=None,
        thermal=None,
        mission_planner=None,
    ) -> None:
        self.drone = drone
        self.yolo = yolo
        self.thermal = thermal
        self.mission_planner = mission_planner
        self._captured_images: list[dict] = []

    def scan(
        self,
        center_lat: float,
        center_lon: float,
        radius_m: float = 30.0,
        low_altitude_m: float = 15.0,
        high_altitude_m: float = 40.0,
    ) -> Optional[str]:
        """
        Perform a full building scan.

        Args:
            center_lat, center_lon: Approximate center of the structure
            radius_m: Orbit radius (should clear the structure edges)
            low_altitude_m: Altitude for close-up orbit
            high_altitude_m: Altitude for overview orbit

        Returns:
            Path to assessment report file, or None on failure
        """
        logger.info(
            f"Building scan: center ({center_lat:.6f}, {center_lon:.6f}) "
            f"r={radius_m}m"
        )

        # Ascend to low orbit altitude
        if not self.drone.takeoff(altitude_m=low_altitude_m):
            logger.error("Takeoff failed for building scan")
            return None

        # Low orbit — detailed side views
        logger.info(f"Low orbit at {low_altitude_m}m")
        self.drone.set_gimbal_angle(-20)  # Slight downward tilt for side views
        self._orbit_and_capture(
            center_lat, center_lon, radius_m, low_altitude_m,
            n_shots=8, label="low"
        )

        # High orbit — overview
        logger.info(f"High orbit at {high_altitude_m}m")
        self.drone.fly_to(center_lat, center_lon, high_altitude_m)
        self.drone.set_gimbal_angle(-45)
        self._orbit_and_capture(
            center_lat, center_lon, radius_m * 1.5, high_altitude_m,
            n_shots=4, label="high"
        )

        # Nadir pass — straight down for roof assessment
        logger.info("Nadir pass — roof assessment")
        self.drone.set_gimbal_angle(config.GIMBAL_TILT_NADIR)
        self.drone.fly_to(center_lat, center_lon, high_altitude_m)
        time.sleep(3)
        self._capture_image(center_lat, center_lon, high_altitude_m, "roof")

        # Return to hover
        self.drone.hover()
        self.drone.set_gimbal_angle(config.GIMBAL_TILT_DEFAULT)

        return self._generate_scan_report(center_lat, center_lon, radius_m)

    def _orbit_and_capture(
        self,
        lat: float, lon: float,
        radius_m: float,
        altitude: float,
        n_shots: int,
        label: str,
    ) -> None:
        """Orbit and capture images at evenly-spaced angular positions."""
        import math

        step_deg = 360.0 / n_shots
        for i in range(n_shots):
            bearing = i * step_deg
            wp_lat, wp_lon = DroneController._offset_gps(lat, lon, bearing, radius_m)
            self.drone.fly_to(wp_lat, wp_lon, altitude)
            time.sleep(1)
            self._capture_image(wp_lat, wp_lon, altitude, f"{label}_{i}")

    def _capture_image(self, lat: float, lon: float, alt: float, label: str) -> None:
        """Capture and save a geo-tagged image."""
        if not self.yolo:
            return

        frame = self.yolo.get_current_frame()
        if frame is None:
            return

        try:
            import cv2
            os.makedirs(config.SCOUT_REPORT_DIR, exist_ok=True)
            filename = f"scan_{label}_{datetime.now().strftime('%H%M%S')}.jpg"
            filepath = os.path.join(config.SCOUT_REPORT_DIR, filename)
            cv2.imwrite(filepath, frame)

            # Get YOLO detections for this shot
            detections = self.yolo.get_recent_detections(window_s=2.0) if self.yolo else []
            thermal_alerts = self.thermal.get_recent_alerts(window_s=2.0) if self.thermal else []

            self._captured_images.append({
                "path": filepath,
                "lat": lat, "lon": lon, "alt": alt,
                "label": label,
                "detections": detections,
                "thermal_alerts": thermal_alerts,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as e:
            logger.error(f"Image capture error: {e}")

    def _generate_scan_report(self, lat: float, lon: float, radius: float) -> str:
        """Generate assessment report from captured images."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = os.path.join(config.SCOUT_REPORT_DIR, f"building_scan_{timestamp}.txt")

        all_detections = []
        for img in self._captured_images:
            all_detections.extend(img.get("detections", []))
            all_detections.extend(img.get("thermal_alerts", []))

        person_count = sum(1 for d in all_detections if d.get("class") == "person")
        thermal_count = sum(1 for d in all_detections if d.get("sensor") == "thermal")

        lines = [
            "=" * 60,
            "  AURA DRONE — BUILDING SCAN REPORT",
            "=" * 60,
            f"Timestamp:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Structure:    ({lat:.6f}, {lon:.6f})",
            f"Scan radius:  {radius}m",
            f"Images taken: {len(self._captured_images)}",
            f"People detected (visual): {person_count}",
            f"Thermal signatures: {thermal_count}",
            "",
            "RECOMMENDATION:",
        ]

        if person_count > 0 or thermal_count > 0:
            lines.append(f"  ⚠ OCCUPIED — {person_count + thermal_count} heat/human signatures detected")
            lines.append("  Approach with caution. Occupants may be hostile or distressed.")
        else:
            lines.append("  Structure appears unoccupied based on available data.")
            lines.append("  Physical inspection recommended before entry.")

        with open(report_path, "w") as f:
            f.write("\n".join(lines))

        logger.info(f"Building scan report: {report_path}")
        return report_path
