"""
ai/threat_assessor.py — Real-Time Threat Classification

Classifies detection events by threat level and urgency.
Determines whether operator immediate action is required.
Uses simple rule logic for speed (this runs on every detection event).
Escalates to AI reasoning only for ambiguous high-stakes situations.
"""

import logging
import time
from typing import Optional

import config

logger = logging.getLogger("AURA.threat")


class ThreatAssessor:
    """
    Rapid threat classification for incoming detection events.

    Threat levels:
        NONE     — Background/non-threatening object (bird, tree)
        LOW      — Monitor only (lone person far away)
        MEDIUM   — Alert operator, await instruction (group, vehicle approaching)
        HIGH     — Urgent operator action required (armed threat indicators, perimeter breach)
        CRITICAL — Immediate defensive action (direct approach at speed)

    For now AURA never takes autonomous offensive action — it only advises.
    The operator makes all engagement decisions.
    """

    # Points per class toward threat score
    THREAT_WEIGHTS = {
        "person": 10,
        "truck": 8,
        "car": 6,
        "motorcycle": 7,
        "bicycle": 3,
        "backpack": 2,
        "boat": 5,
        "thermal": 12,  # Thermal signature in thermal_alert
    }

    # Thresholds for threat levels
    LEVEL_THRESHOLDS = [
        (50, "critical"),
        (30, "high"),
        (15, "medium"),
        (5, "low"),
        (0, "none"),
    ]

    def __init__(self) -> None:
        # Rolling window of scored detections to track escalation
        self._recent_scores: list[dict] = []
        self._window_s = 30.0

    def assess(self, detection: dict) -> dict:
        """
        Assess a single detection event for threat level and urgency.

        Args:
            detection: Detection dict from YOLO or thermal (must have 'class' or 'sensor')

        Returns:
            dict with:
                "threat_level": str
                "score": int
                "urgent": bool (True = notify operator immediately)
                "recommendation": str
        """
        now = time.time()
        cls = detection.get("class", detection.get("sensor", "unknown"))
        confidence = detection.get("confidence", 1.0)

        # Base score from class weight
        base_weight = self.THREAT_WEIGHTS.get(cls, 0)
        score = int(base_weight * confidence)

        # Multiple persons in same window escalates threat
        recent_persons = self._count_recent(cls, window_s=10.0)
        if cls == "person" and recent_persons >= 3:
            score += 20  # Group escalation
        elif cls == "thermal" and recent_persons >= 2:
            score += 15

        # Add to rolling history
        self._recent_scores.append({
            "class": cls,
            "score": score,
            "timestamp": now,
        })
        # Clean old entries
        self._recent_scores = [
            e for e in self._recent_scores
            if now - e["timestamp"] <= self._window_s
        ]

        # Determine level
        threat_level = self._score_to_level(score)

        urgent = threat_level in ("high", "critical")

        recommendation = self._get_recommendation(
            cls, score, threat_level, recent_persons
        )

        result = {
            "threat_level": threat_level,
            "score": score,
            "urgent": urgent,
            "class": cls,
            "recommendation": recommendation,
            "timestamp": now,
        }

        if urgent:
            logger.warning(
                f"URGENT THREAT: {cls} | level={threat_level} | score={score} | {recommendation}"
            )

        return result

    def get_current_threat_level(self) -> str:
        """
        Return the current overall threat level based on recent history.
        Use this for situation reports, not individual detections.
        """
        now = time.time()
        recent = [
            e for e in self._recent_scores
            if now - e["timestamp"] <= 15.0
        ]
        if not recent:
            return "none"

        total_score = sum(e["score"] for e in recent)
        return self._score_to_level(total_score)

    @classmethod
    def _score_to_level(cls, score: int) -> str:
        for threshold, level in cls.LEVEL_THRESHOLDS:
            if score >= threshold:
                return level
        return "none"

    def _count_recent(self, cls: str, window_s: float) -> int:
        """Count detections of a given class in the recent window."""
        cutoff = time.time() - window_s
        return sum(
            1 for e in self._recent_scores
            if e["class"] == cls and e["timestamp"] >= cutoff
        )

    @staticmethod
    def _get_recommendation(
        cls: str, score: int, level: str, count: int
    ) -> str:
        """Generate a concise operator recommendation."""
        if level == "none":
            return "No action required"
        if level == "low":
            return f"Monitor {cls} — continue mission"
        if level == "medium":
            if cls == "person":
                return f"Observe {count} person(s) — maintain altitude, do not approach"
            if cls in ("car", "truck"):
                return "Vehicle detected — track movement, alert if approaching camp"
            return f"{cls} detected — maintain observation"
        if level == "high":
            if cls == "thermal":
                return "Multiple heat signatures — possible hostile group, alert operator immediately"
            if cls == "person":
                return f"Group of {count}+ detected — hover and report, operator decide action"
            return f"Elevated threat: {cls} — operator action required"
        if level == "critical":
            return "CRITICAL THREAT — operator must respond immediately"
        return "Assess situation manually"
