"""
ai/mission_planner.py — Ollama-Powered Mission Planning

Uses DeepSeek-R1:8b for deep reasoning tasks:
- Converting natural-language mission goals to structured action steps
- Generating GPS patrol route waypoints from area descriptions
- Assessing tactical situation from combined sensor data

Results are cached by input hash to avoid redundant AI queries
(patrol route for "north field" shouldn't re-query every time).
"""

import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Optional

import requests

import config

logger = logging.getLogger("AURA.mission_planner")


class MissionPlanner:
    """
    AI mission planning interface via Ollama DeepSeek-R1.

    All methods degrade gracefully if Ollama is unreachable:
    - plan_mission() returns a minimal "return home and wait" plan
    - assess_situation() returns a simple telemetry summary
    - plan_patrol_route() returns a default square around current position

    The reasoning model (DeepSeek-R1) is slow but thorough.
    Only use it for planning decisions, not for real-time classification.
    """

    SYSTEM_PROMPT = (
        "You are AURA, an AI embedded in a survival drone. "
        "Your operator is in a post-disaster or SHTF scenario. "
        "You provide tactical, actionable intelligence. "
        "Be concise and direct — your operator may be under stress. "
        "Always prioritize operator safety over mission success. "
        "When providing GPS coordinates or structured data, use JSON format."
    )

    def __init__(self) -> None:
        self._cache: OrderedDict = OrderedDict()

    # ──────────────────────────────────────────
    # Mission Planning
    # ──────────────────────────────────────────

    def plan_mission(self, goal_text: str) -> dict:
        """
        Convert a natural-language mission goal into structured steps.

        Args:
            goal_text: e.g. "Scout the road north of our camp for threats"

        Returns:
            dict with keys:
                "steps": list of action dicts
                "priority": "high" | "medium" | "low"
                "estimated_duration_min": int
                "risks": list of strings
                "raw_response": str (full AI response)
        """
        prompt = (
            f"{self.SYSTEM_PROMPT}\n\n"
            f"Plan this mission: {goal_text}\n\n"
            "Respond in JSON with this structure:\n"
            "{\n"
            '  "steps": [\n'
            '    {"action": "takeoff", "params": {"altitude": 50}},\n'
            '    {"action": "fly_to", "params": {"lat": 0.0, "lon": 0.0, "alt": 50}},\n'
            "    ...\n"
            "  ],\n"
            '  "priority": "high",\n'
            '  "estimated_duration_min": 15,\n'
            '  "risks": ["string", ...]\n'
            "}"
        )

        cached = self._get_cached(prompt)
        if cached:
            return cached

        response = self._query_ollama(
            model=config.OLLAMA_REASONING_MODEL,
            prompt=prompt,
        )

        if response:
            try:
                # Extract JSON from response (DeepSeek may wrap it in text)
                parsed = self._extract_json(response)
                result = parsed if parsed else {
                    "steps": [{"action": "hover"}],
                    "priority": "low",
                    "estimated_duration_min": 0,
                    "risks": ["AI planning failed — manual control required"],
                    "raw_response": response,
                }
                self._set_cached(prompt, result)
                return result
            except Exception as e:
                logger.error(f"Failed to parse mission plan: {e}")

        # Fallback safe plan
        return {
            "steps": [
                {"action": "hover"},
                {"action": "situation_report"},
            ],
            "priority": "low",
            "estimated_duration_min": 5,
            "risks": ["AI unavailable — conservative plan applied"],
            "raw_response": "AI unavailable",
        }

    # ──────────────────────────────────────────
    # Situation Assessment
    # ──────────────────────────────────────────

    def assess_situation(
        self,
        telemetry: dict,
        detections: list[dict],
        thermal_alerts: list[dict],
    ) -> dict:
        """
        Generate a comprehensive situation assessment from sensor data.

        Args:
            telemetry: Current drone state dict from DroneController.get_telemetry()
            detections: Recent YOLO detection list
            thermal_alerts: Recent thermal alert list

        Returns:
            dict with "assessment", "threat_level", "recommendations"
        """
        # Summarize detections for the prompt
        detection_summary = {}
        for d in detections:
            cls = d.get("class", "unknown")
            detection_summary[cls] = detection_summary.get(cls, 0) + 1

        context = {
            "battery": telemetry.get("battery_percent", "?"),
            "altitude": telemetry.get("altitude_m", 0),
            "location": f"({telemetry.get('latitude', 0):.5f}, {telemetry.get('longitude', 0):.5f})",
            "mode": telemetry.get("mode", "?"),
            "detections": detection_summary,
            "thermal_contacts": len(thermal_alerts),
        }

        prompt = (
            f"{self.SYSTEM_PROMPT}\n\n"
            f"Current drone status: {json.dumps(context)}\n\n"
            "Provide a situation assessment in JSON:\n"
            "{\n"
            '  "assessment": "brief situation description",\n'
            '  "threat_level": "none|low|medium|high|critical",\n'
            '  "recommendations": ["action1", "action2", ...]\n'
            "}"
        )

        response = self._query_ollama(
            model=config.OLLAMA_MAIN_MODEL,  # Faster model for situation reports
            prompt=prompt,
        )

        if response:
            parsed = self._extract_json(response)
            if parsed:
                return parsed

        # Fallback: rule-based assessment
        return self._rule_based_assessment(context)

    # ──────────────────────────────────────────
    # Route Planning
    # ──────────────────────────────────────────

    def plan_patrol_route(
        self,
        area_description: str,
        num_waypoints: int = 6,
        center_lat: float = 0.0,
        center_lon: float = 0.0,
    ) -> list[dict]:
        """
        Generate GPS waypoints for patrolling an area.

        Args:
            area_description: Natural language description ("north treeline", "around the barn")
            num_waypoints: Number of patrol waypoints to generate
            center_lat, center_lon: Current position for relative planning

        Returns:
            List of waypoint dicts: [{"lat": ..., "lon": ..., "alt": ...}, ...]
        """
        prompt = (
            f"{self.SYSTEM_PROMPT}\n\n"
            f"Generate a {num_waypoints}-waypoint patrol route for: {area_description}\n"
            f"Current position: ({center_lat:.5f}, {center_lon:.5f})\n"
            f"Patrol altitude: {config.PATROL_ALTITUDE_M}m\n\n"
            "Respond in JSON:\n"
            '{"waypoints": [{"lat": 0.0, "lon": 0.0, "alt": 30}, ...]}'
        )

        cached = self._get_cached(prompt)
        if cached:
            return cached

        response = self._query_ollama(
            model=config.OLLAMA_REASONING_MODEL,
            prompt=prompt,
        )

        if response:
            parsed = self._extract_json(response)
            if parsed and "waypoints" in parsed:
                waypoints = parsed["waypoints"]
                self._set_cached(prompt, waypoints)
                return waypoints

        # Fallback: generate a default square patrol around current position
        logger.warning("AI route planning failed — generating default square patrol")
        return self._default_square_patrol(center_lat, center_lon, num_waypoints)

    # ──────────────────────────────────────────
    # Ollama Interface
    # ──────────────────────────────────────────

    def _query_ollama(self, model: str, prompt: str) -> Optional[str]:
        """
        Send a query to Ollama and return the response text.

        Returns:
            Response string, or None if request failed
        """
        try:
            response = requests.post(
                config.OLLAMA_URL,
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,   # Low temp for consistent tactical output
                        "num_predict": 1024,
                    },
                },
                timeout=config.OLLAMA_TIMEOUT_S,
            )

            if response.ok:
                data = response.json()
                return data.get("response", "").strip()
            else:
                logger.warning(f"Ollama HTTP {response.status_code}")
                return None

        except requests.exceptions.ConnectionError:
            logger.warning("Ollama not reachable — using fallback logic")
            return None
        except requests.exceptions.Timeout:
            logger.warning("Ollama timeout")
            return None
        except Exception as e:
            logger.error(f"Ollama query error: {e}")
            return None

    # ──────────────────────────────────────────
    # Cache
    # ──────────────────────────────────────────

    def _get_cached(self, prompt: str):
        """Retrieve cached result for identical prompt (LRU eviction)."""
        key = hashlib.md5(prompt.encode()).hexdigest()
        return self._cache.get(key)

    def _set_cached(self, prompt: str, value) -> None:
        """Cache a result, evicting oldest if at capacity."""
        key = hashlib.md5(prompt.encode()).hexdigest()
        if len(self._cache) >= config.OLLAMA_CACHE_MAX_ENTRIES:
            self._cache.popitem(last=False)  # Remove oldest
        self._cache[key] = value

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """
        Extract JSON from a response that may contain surrounding text.
        DeepSeek-R1 often wraps JSON in markdown code blocks or thinking text.
        """
        import re

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Look for JSON block in markdown ```json ... ```
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Look for first { ... } block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _rule_based_assessment(context: dict) -> dict:
        """Produce a basic situation assessment without AI."""
        threat_count = context.get("detections", {})
        thermal = context.get("thermal_contacts", 0)
        battery = context.get("battery", 100)

        persons = threat_count.get("person", 0)
        vehicles = sum(threat_count.get(c, 0) for c in ["car", "truck", "motorcycle"])

        threat_level = "none"
        if thermal > 0 or persons > 0:
            threat_level = "medium"
        if persons > 2 or vehicles > 1:
            threat_level = "high"
        if battery < 20:
            threat_level = "critical" if threat_level != "none" else "low"

        recommendations = []
        if battery < 30:
            recommendations.append("Return to home — low battery")
        if persons > 0:
            recommendations.append(f"Maintain observation — {persons} person(s) detected")
        if thermal > 0:
            recommendations.append("Investigate thermal contact")

        return {
            "assessment": (
                f"Battery: {battery}%. "
                f"Visual: {persons} persons, {vehicles} vehicles. "
                f"Thermal: {thermal} contacts."
            ),
            "threat_level": threat_level,
            "recommendations": recommendations or ["Continue monitoring"],
        }

    @staticmethod
    def _default_square_patrol(
        lat: float, lon: float, n_points: int
    ) -> list[dict]:
        """Generate a simple square patrol 100m around current position."""
        import math
        offsets_m = 100.0
        waypoints = []
        step = 360.0 / n_points
        for i in range(n_points):
            bearing = i * step
            wp_lat, wp_lon = DroneController_offset(lat, lon, bearing, offsets_m)
            waypoints.append({"lat": wp_lat, "lon": wp_lon, "alt": config.PATROL_ALTITUDE_M})
        return waypoints


def DroneController_offset(lat, lon, bearing_deg, distance_m):
    """Standalone GPS offset (avoids circular import from drone_control)."""
    import math
    R = 6371000
    bearing = math.radians(bearing_deg)
    lat1, lon1 = math.radians(lat), math.radians(lon)
    d = distance_m / R
    lat2 = math.asin(math.sin(lat1) * math.cos(d) +
                      math.cos(lat1) * math.sin(d) * math.cos(bearing))
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lon2)
