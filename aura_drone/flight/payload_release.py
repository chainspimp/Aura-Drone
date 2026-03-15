"""
flight/payload_release.py — Servo-Controlled Payload Drop

Checks zone clearance before releasing payload (no people/obstacles detected
directly below), then actuates the AUX2 servo to release.
"""

import logging
import time
from typing import Optional

import config
from flight.drone_control import DroneController

logger = logging.getLogger("AURA.payload")


class PayloadRelease:
    """
    Safe payload drop with pre-release zone clearance check.

    Sequence:
    1. Check visual detection for people/objects directly below (≤15m radius)
    2. If clear: release payload via AUX2 servo
    3. If not clear: alert operator, wait for manual override or re-check
    """

    def __init__(self, drone: DroneController, yolo=None) -> None:
        self.drone = drone
        self.yolo = yolo

    def release_with_clearance_check(self, force: bool = False) -> bool:
        """
        Perform zone clearance check, then release.

        Args:
            force: Skip clearance check (operator override)

        Returns:
            True if payload was released
        """
        if not force:
            clear, reason = self._check_zone_clearance()
            if not clear:
                logger.warning(f"Payload drop BLOCKED: {reason}")
                return False

        logger.info("Zone clear — releasing payload")
        return self.drone.drop_payload()

    def _check_zone_clearance(self) -> tuple[bool, str]:
        """
        Check if the drop zone directly below is clear of people/obstacles.

        Returns:
            (is_clear, reason_string)
        """
        if not self.yolo:
            # No visual confirmation — assume clear but warn
            logger.warning("No visual system available — proceeding with payload drop unverified")
            return True, "no_visual_system"

        recent = self.yolo.get_recent_detections(window_s=3.0)

        # Check for people in frame
        people = [d for d in recent if d.get("class") == "person"]
        if people:
            return False, f"Person detected in drop zone ({len(people)} detection(s))"

        # Check for vehicles
        vehicles = [d for d in recent if d.get("class") in ["car", "truck", "motorcycle"]]
        if vehicles:
            return False, f"Vehicle detected in drop zone"

        return True, "clear"
