"""
vision/resource_finder.py — Aerial Resource Detection

Detects survival resources from the air: water sources, agricultural areas,
supply caches, and abandoned vehicles with potential salvage value.
Uses YOLO for objects and color/texture analysis for water/vegetation.
"""

import logging
import os
import time
from datetime import datetime
from typing import Optional

import numpy as np

import config
from flight.drone_control import DroneController

logger = logging.getLogger("AURA.resource_finder")

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


class ResourceFinder:
    """
    Aerial detection of survival-critical resources.

    Detection methods:
    - Water: Blue/green color segmentation + texture analysis (flat, reflective)
    - Vegetation/crops: NDVI proxy via color ratios in visible spectrum
    - Supply caches: YOLO detection of backpacks, containers, vehicles
    - Structures: YOLO + edge detection for roof patterns

    All findings are GPS-tagged and saved to the scout reports directory.
    """

    # Color ranges for resource detection (HSV)
    WATER_HSV_LOWER = np.array([90, 30, 30])    # Blue water range
    WATER_HSV_UPPER = np.array([130, 255, 255])
    VEGETATION_HSV_LOWER = np.array([35, 50, 30])   # Green vegetation
    VEGETATION_HSV_UPPER = np.array([85, 255, 255])

    # Minimum pixel area to report a resource (filters noise)
    MIN_WATER_AREA_PX = 500
    MIN_VEGETATION_AREA_PX = 1000

    def __init__(self, drone: DroneController, yolo=None) -> None:
        self.drone = drone
        self.yolo = yolo
        self._found_resources: list[dict] = []

    def scan_area(
        self,
        center_lat: float,
        center_lon: float,
        search_radius_m: float = 200.0,
    ) -> list[dict]:
        """
        Perform a systematic resource scan of an area.

        Args:
            center_lat, center_lon: Center of search area
            search_radius_m: Radius to search from center point

        Returns:
            List of resource dicts: [{"type": "water", "lat": ..., "lon": ..., ...}, ...]
        """
        self._found_resources = []
        logger.info(
            f"Resource scan: center ({center_lat:.6f}, {center_lon:.6f}) "
            f"r={search_radius_m}m"
        )

        # Ascend to scout altitude for wide-area coverage
        self.drone.takeoff(altitude_m=config.SCOUT_ALTITUDE_M)
        self.drone.set_gimbal_angle(config.GIMBAL_TILT_NADIR)

        # Spiral outward from center
        waypoints = self._generate_spiral(center_lat, center_lon, search_radius_m)

        for wp in waypoints:
            self.drone.fly_to(wp["lat"], wp["lon"], config.SCOUT_ALTITUDE_M)
            time.sleep(1)
            self._analyze_position(wp["lat"], wp["lon"])

        self.drone.return_home()
        self._save_resource_log()
        return self._found_resources

    def analyze_frame(
        self, frame: np.ndarray, lat: float, lon: float
    ) -> list[dict]:
        """
        Analyze a single frame for resources at the given GPS position.
        Can be called inline during other missions.

        Returns:
            List of resources detected in this frame
        """
        found = []

        if not CV2_AVAILABLE or frame is None:
            return found

        # Color-based water detection
        water_result = self._detect_water(frame, lat, lon)
        if water_result:
            found.append(water_result)

        # Vegetation / crop detection
        veg_result = self._detect_vegetation(frame, lat, lon)
        if veg_result:
            found.append(veg_result)

        # YOLO-based object detection (supply items)
        if self.yolo:
            detections = self.yolo.get_recent_detections(window_s=2.0)
            for det in detections:
                if det.get("class") in config.YOLO_RESOURCE_CLASSES:
                    found.append({
                        "type": det["class"],
                        "lat": lat,
                        "lon": lon,
                        "confidence": det.get("confidence", 0),
                        "source": "yolo",
                        "timestamp": datetime.now().isoformat(),
                    })

        if found:
            self._found_resources.extend(found)

        return found

    def _analyze_position(self, lat: float, lon: float) -> None:
        """Capture frame and analyze at current GPS position."""
        if not self.yolo:
            return
        frame = self.yolo.get_current_frame()
        if frame is not None:
            results = self.analyze_frame(frame, lat, lon)
            for r in results:
                logger.info(f"Resource found: {r['type']} @ ({lat:.6f}, {lon:.6f})")

    def _detect_water(
        self, frame: np.ndarray, lat: float, lon: float
    ) -> Optional[dict]:
        """
        Detect open water using HSV color segmentation.
        Water appears blue-green from above (color depends on depth, sediment).
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.WATER_HSV_LOWER, self.WATER_HSV_UPPER)

        # Clean up mask
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        water_pixels = int(mask.sum() / 255)
        total_pixels = frame.shape[0] * frame.shape[1]
        water_fraction = water_pixels / total_pixels

        if water_pixels >= self.MIN_WATER_AREA_PX:
            return {
                "type": "water",
                "lat": lat,
                "lon": lon,
                "coverage_percent": round(water_fraction * 100, 1),
                "pixel_area": water_pixels,
                "source": "color_analysis",
                "timestamp": datetime.now().isoformat(),
                "notes": "Open water detected — verify before drinking (filter required)",
            }
        return None

    def _detect_vegetation(
        self, frame: np.ndarray, lat: float, lon: float
    ) -> Optional[dict]:
        """
        Detect green vegetation/crops.
        High green coverage may indicate agricultural land (food source).
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.VEGETATION_HSV_LOWER, self.VEGETATION_HSV_UPPER)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        veg_pixels = int(mask.sum() / 255)
        total_pixels = frame.shape[0] * frame.shape[1]
        veg_fraction = veg_pixels / total_pixels

        # Only report if substantial vegetation (>10% of frame)
        if veg_pixels >= self.MIN_VEGETATION_AREA_PX and veg_fraction > 0.10:
            veg_type = "crops/farmland" if veg_fraction > 0.40 else "vegetation"
            return {
                "type": veg_type,
                "lat": lat,
                "lon": lon,
                "coverage_percent": round(veg_fraction * 100, 1),
                "source": "color_analysis",
                "timestamp": datetime.now().isoformat(),
                "notes": "Green vegetation — potential food/shelter resource",
            }
        return None

    def _generate_spiral(
        self, center_lat: float, center_lon: float, max_radius_m: float
    ) -> list[dict]:
        """
        Generate spiral search pattern waypoints.
        Starts at center, expands outward — maximizes early coverage near base.
        """
        import math

        waypoints = []
        arms = 3   # Archimedean spiral arms
        points_per_arm = 8
        step_m = max_radius_m / points_per_arm

        for i in range(points_per_arm):
            radius = step_m * (i + 1)
            for j in range(arms):
                angle = (360.0 / arms) * j + (i * 15)  # Slight offset per ring
                lat, lon = DroneController._offset_gps(
                    center_lat, center_lon, angle, radius
                )
                waypoints.append({"lat": lat, "lon": lon})

        return waypoints

    def _save_resource_log(self) -> None:
        """Save found resources to JSON log file."""
        import json
        if not self._found_resources:
            return

        os.makedirs(config.SCOUT_REPORT_DIR, exist_ok=True)
        filename = f"resources_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(config.SCOUT_REPORT_DIR, filename)

        with open(filepath, "w") as f:
            json.dump(self._found_resources, f, indent=2)

        logger.info(
            f"Resource scan complete: {len(self._found_resources)} resources found → {filepath}"
        )

    def get_found_resources(self) -> list[dict]:
        """Return all resources found in the most recent scan."""
        return list(self._found_resources)
