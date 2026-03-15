"""
flight/route_scout.py — Corridor Route Scout

Flies a systematic S-pattern scan of a corridor defined by start/end GPS
coordinates. Captures geo-tagged images every SCOUT_PHOTO_INTERVAL_M meters,
runs YOLO on each, then generates an Ollama-powered text report on return.
"""

import json
import logging
import math
import os
import time
from datetime import datetime
from typing import Optional

import config
from flight.drone_control import DroneController

logger = logging.getLogger("AURA.route_scout")


class RouteScout:
    """
    Systematic aerial reconnaissance of a corridor.

    S-Pattern Logic:
    ───────────────
    Given start/end points and corridor width W:
    1. Divide corridor into parallel lanes of width W
    2. Fly each lane left-to-right alternating direction (S-pattern)
    3. At each lane end, step laterally to next lane
    4. Capture image and run inference every SCOUT_PHOTO_INTERVAL_M meters

    On completion, generate a text report via Ollama summarizing all findings.
    Output: scout_report_{timestamp}.txt + detection_log (JSON)
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
        self._detections: list[dict] = []
        self._photos_taken: int = 0

    def run(
        self,
        start: dict,
        end: dict,
        corridor_width_m: float = 50.0,
    ) -> Optional[str]:
        """
        Execute the scout mission.

        Args:
            start: {"lat": float, "lon": float} — Start point
            end:   {"lat": float, "lon": float} — End point
            corridor_width_m: Width of each scan lane in meters

        Returns:
            Path to generated scout report file, or None on failure
        """
        logger.info(
            f"Scout mission: ({start['lat']:.6f},{start['lon']:.6f}) → "
            f"({end['lat']:.6f},{end['lon']:.6f}) | Lane width: {corridor_width_m}m"
        )

        # Ascend to scout altitude
        telemetry = self.drone.get_telemetry()
        if telemetry.get("altitude_m", 0) < 2.0:
            if not self.drone.takeoff(altitude_m=config.SCOUT_ALTITUDE_M):
                logger.error("Takeoff failed — aborting scout")
                return None

        # Tilt gimbal for downward imaging
        self.drone.set_gimbal_angle(config.GIMBAL_TILT_NADIR)
        time.sleep(2)  # Allow gimbal to settle

        # Generate S-pattern waypoints
        waypoints = self._generate_s_pattern(start, end, corridor_width_m)
        logger.info(f"S-pattern generated: {len(waypoints)} waypoints")

        # Execute waypoint flight with image capture
        self._execute_s_pattern(waypoints)

        # Restore gimbal to patrol angle for return
        self.drone.set_gimbal_angle(config.GIMBAL_TILT_DEFAULT)

        # Return to home
        logger.info("Scout complete — returning home")
        self.drone.return_home()

        # Generate report
        report_path = self._generate_report(start, end, corridor_width_m)
        return report_path

    def _generate_s_pattern(
        self, start: dict, end: dict, corridor_width_m: float
    ) -> list[dict]:
        """
        Compute S-pattern waypoints for the corridor.

        The corridor is divided perpendicular to the start→end bearing.
        Each pass covers one lane width with overlap for consistent coverage.

        Returns:
            List of waypoints: [{"lat": float, "lon": float}, ...]
        """
        # Total corridor length
        total_length_m = DroneController._haversine(
            start["lat"], start["lon"], end["lat"], end["lon"]
        )

        # Bearing from start to end
        bearing = DroneController._bearing(
            start["lat"], start["lon"], end["lat"], end["lon"]
        )
        perp_bearing_right = (bearing + 90) % 360
        perp_bearing_left = (bearing - 90) % 360

        # Half the total corridor width (scouts are centered on start-end line)
        half_width = corridor_width_m / 2

        # Number of lanes
        lane_width = corridor_width_m
        n_lanes = max(1, math.ceil(half_width * 2 / lane_width))

        waypoints = []

        for lane_idx in range(n_lanes):
            # Lateral offset from center line for this lane
            offset_m = -half_width + (lane_idx + 0.5) * lane_width

            # Start and end of this lane
            if offset_m >= 0:
                lane_start = DroneController._offset_gps(
                    start["lat"], start["lon"], perp_bearing_right, offset_m
                )
                lane_end = DroneController._offset_gps(
                    end["lat"], end["lon"], perp_bearing_right, offset_m
                )
            else:
                lane_start = DroneController._offset_gps(
                    start["lat"], start["lon"], perp_bearing_left, abs(offset_m)
                )
                lane_end = DroneController._offset_gps(
                    end["lat"], end["lon"], perp_bearing_left, abs(offset_m)
                )

            # Alternate direction for S-pattern
            if lane_idx % 2 == 0:
                waypoints.append({"lat": lane_start[0], "lon": lane_start[1]})
                waypoints.append({"lat": lane_end[0], "lon": lane_end[1]})
            else:
                waypoints.append({"lat": lane_end[0], "lon": lane_end[1]})
                waypoints.append({"lat": lane_start[0], "lon": lane_start[1]})

        return waypoints

    def _execute_s_pattern(self, waypoints: list[dict]) -> None:
        """
        Fly each waypoint, triggering image capture at distance intervals.
        """
        altitude = config.SCOUT_ALTITUDE_M
        prev_lat = waypoints[0]["lat"]
        prev_lon = waypoints[0]["lon"]
        distance_since_capture = 0.0

        for i, wp in enumerate(waypoints):
            logger.info(f"Scout waypoint {i + 1}/{len(waypoints)}")

            self.drone.fly_to(
                lat=wp["lat"],
                lon=wp["lon"],
                alt=altitude,
            )

            # Calculate distance traveled for this leg
            dist = DroneController._haversine(
                prev_lat, prev_lon, wp["lat"], wp["lon"]
            )
            distance_since_capture += dist

            # Capture image(s) along this leg at interval
            while distance_since_capture >= config.SCOUT_PHOTO_INTERVAL_M:
                self._capture_and_analyze(wp["lat"], wp["lon"])
                distance_since_capture -= config.SCOUT_PHOTO_INTERVAL_M

            prev_lat, prev_lon = wp["lat"], wp["lon"]

    def _capture_and_analyze(self, lat: float, lon: float) -> None:
        """
        Capture a frame from the main camera, run YOLO inference,
        log all detections with GPS coordinates.
        """
        timestamp = datetime.now().isoformat()
        self._photos_taken += 1

        # Capture frame from YOLO watcher (avoids duplicate camera access)
        frame = None
        if self.yolo:
            frame = self.yolo.get_current_frame()

        # Save frame to disk
        if frame is not None:
            import cv2
            os.makedirs(config.SCOUT_REPORT_DIR, exist_ok=True)
            filename = f"scout_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
            filepath = os.path.join(config.SCOUT_REPORT_DIR, filename)
            cv2.imwrite(filepath, frame)

        # Get YOLO detections for this location
        if self.yolo:
            recent_detections = self.yolo.get_recent_detections(window_s=2.0)
            for det in recent_detections:
                det["gps"] = {"lat": lat, "lon": lon, "alt": config.SCOUT_ALTITUDE_M}
                det["timestamp"] = timestamp
                det["photo_index"] = self._photos_taken
                self._detections.append(det)

                if det.get("class") in config.YOLO_ALERT_CLASSES:
                    logger.info(
                        f"Scout detection: {det['class']} ({det.get('confidence', 0):.0%}) "
                        f"@ ({lat:.6f}, {lon:.6f})"
                    )

        # Check thermal for human signatures
        if self.thermal:
            thermal_alerts = self.thermal.get_recent_alerts(window_s=2.0)
            for alert in thermal_alerts:
                alert["gps"] = {"lat": lat, "lon": lon}
                alert["timestamp"] = timestamp
                alert["sensor"] = "thermal"
                self._detections.append(alert)
                logger.info(f"Scout thermal hit @ ({lat:.6f}, {lon:.6f})")

    def _generate_report(
        self, start: dict, end: dict, corridor_width_m: float
    ) -> str:
        """
        Generate a text summary of the scout mission using Ollama (if available),
        falling back to a structured plain-text report.

        Returns:
            Filepath of the report file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"scout_report_{timestamp}.txt"
        report_path = os.path.join(config.SCOUT_REPORT_DIR, report_filename)

        # Save raw detections as JSON
        json_path = os.path.join(config.SCOUT_REPORT_DIR, f"scout_detections_{timestamp}.json")
        with open(json_path, "w") as f:
            json.dump(self._detections, f, indent=2)

        # Summarize detection classes
        class_counts: dict = {}
        for det in self._detections:
            cls = det.get("class", det.get("sensor", "unknown"))
            class_counts[cls] = class_counts.get(cls, 0) + 1

        threat_count = sum(
            count for cls, count in class_counts.items()
            if cls in config.YOLO_THREAT_CLASSES
        )

        summary_lines = [
            "=" * 60,
            "  AURA DRONE — SCOUT MISSION REPORT",
            "=" * 60,
            f"Timestamp:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Start:        ({start['lat']:.6f}, {start['lon']:.6f})",
            f"End:          ({end['lat']:.6f}, {end['lon']:.6f})",
            f"Corridor:     {corridor_width_m}m wide",
            f"Altitude:     {config.SCOUT_ALTITUDE_M}m AGL",
            f"Photos taken: {self._photos_taken}",
            f"Total detections: {len(self._detections)}",
            f"Threat detections: {threat_count}",
            "",
            "DETECTION SUMMARY:",
        ]

        for cls, count in sorted(class_counts.items(), key=lambda x: -x[1]):
            summary_lines.append(f"  {cls:20s} {count:3d} events")

        if self._detections:
            summary_lines.append("")
            summary_lines.append("NOTABLE DETECTIONS:")
            shown = 0
            for det in self._detections:
                cls = det.get("class", det.get("sensor", "?"))
                if cls in config.YOLO_ALERT_CLASSES or det.get("sensor") == "thermal":
                    gps = det.get("gps", {})
                    summary_lines.append(
                        f"  [{det.get('timestamp', '?')[:19]}] "
                        f"{cls} @ ({gps.get('lat', 0):.5f}, {gps.get('lon', 0):.5f})"
                    )
                    shown += 1
                    if shown >= 20:
                        summary_lines.append("  ... (see JSON for full list)")
                        break

        # Attempt AI summary if Ollama available
        ai_summary = self._generate_ai_summary(class_counts, threat_count)
        if ai_summary:
            summary_lines.append("")
            summary_lines.append("AI ASSESSMENT:")
            summary_lines.append(ai_summary)

        summary_lines.append("")
        summary_lines.append(f"Raw detections saved to: {json_path}")
        summary_lines.append("=" * 60)

        report_text = "\n".join(summary_lines)

        with open(report_path, "w") as f:
            f.write(report_text)

        logger.info(f"Scout report written to {report_path}")
        print(report_text)
        return report_path

    def _generate_ai_summary(self, class_counts: dict, threat_count: int) -> Optional[str]:
        """
        Ask Ollama to write a natural-language assessment of the scout findings.
        Falls back gracefully if Ollama is unreachable.
        """
        if not self._detections:
            return "Corridor appears clear — no significant contacts detected."

        try:
            import requests

            prompt = (
                f"You are AURA, a survival drone AI assistant. "
                f"Summarize this aerial scout report for the operator in 3-5 sentences. "
                f"Be direct and focus on survival-relevant information.\n\n"
                f"Detections: {json.dumps(class_counts)}\n"
                f"Total threats: {threat_count}\n"
                f"Total contacts: {len(self._detections)}\n"
            )

            response = requests.post(
                config.OLLAMA_URL,
                json={
                    "model": config.OLLAMA_MAIN_MODEL,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=config.OLLAMA_TIMEOUT_S,
            )

            if response.ok:
                return response.json().get("response", "").strip()

        except Exception as e:
            logger.debug(f"AI summary unavailable: {e}")

        return None
