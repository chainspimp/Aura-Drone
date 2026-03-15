"""
ai/command_parser.py — Voice/Text Command → Drone Action Parser

Converts natural-language commands into structured action dictionaries
using Ollama (gemma3n:e2b for speed). Falls back to keyword matching
when AI is unavailable — critical flight commands always work.
"""

import json
import logging
import re
from typing import Optional

import requests

import config

logger = logging.getLogger("AURA.command_parser")


class CommandParser:
    """
    Natural-language command interpreter.

    Primary path: Ollama gemma3n:e2b model → JSON action dict
    Fallback path: Keyword regex rules → JSON action dict

    Supported actions (action field values):
        takeoff, land, return_home, hover, fly_to, orbit,
        patrol, scout, drop_payload, situation_report,
        what_do_you_see, set_relay, send_lora_message,
        set_gimbal, unknown
    """

    SYSTEM_PROMPT = (
        "You parse drone commands into JSON action objects. "
        "Respond ONLY with valid JSON, no other text.\n\n"
        "Action schema:\n"
        '{"action": "action_name", "params": {...}}\n\n'
        "Available actions:\n"
        "- takeoff: params: {altitude: float (default 30)}\n"
        "- land: params: {}\n"
        "- return_home: params: {}\n"
        "- hover: params: {}\n"
        "- fly_to: params: {lat: float, lon: float, alt: float}\n"
        "- orbit: params: {lat: float, lon: float, radius: float, duration: int}\n"
        "- patrol: params: {waypoints: [] (empty means use current)}\n"
        "- scout: params: {start: {lat,lon}, end: {lat,lon}, corridor_width_m: float}\n"
        "- drop_payload: params: {}\n"
        "- situation_report: params: {}\n"
        "- what_do_you_see: params: {}\n"
        "- set_relay: params: {}\n"
        "- set_gimbal: params: {pitch: int (-90 to 0)}\n"
        "- unknown: params: {raw: 'original text'}\n"
    )

    # Regex rules for fallback parsing
    KEYWORD_RULES = [
        (re.compile(r"\b(return|rtl|go home|return home|rth)\b", re.I), "return_home", {}),
        (re.compile(r"\b(land|put down|set down)\b", re.I), "land", {}),
        (re.compile(r"\b(hover|hold|stop|stay)\b", re.I), "hover", {}),
        (re.compile(r"\b(patrol|perimeter|circle camp)\b", re.I), "patrol", {"waypoints": []}),
        (re.compile(r"\b(drop|release|deliver)\b.*\b(payload|package|supply|supplies)\b", re.I), "drop_payload", {}),
        (re.compile(r"\b(sitrep|situation|report|status)\b", re.I), "situation_report", {}),
        (re.compile(r"\b(what|describe|see|detect|observe)\b", re.I), "what_do_you_see", {}),
        (re.compile(r"\b(relay|comms|communication)\b", re.I), "set_relay", {}),
        (re.compile(r"\b(gimbal|camera).*(down|nadir|forward|up)\b", re.I), "set_gimbal", None),
    ]

    def __init__(self) -> None:
        self._ollama_available: Optional[bool] = None  # None = not yet checked

    def check_ollama_available(self) -> bool:
        """
        Ping Ollama to verify it's running.
        Result is cached — call check_ollama_available() to refresh.
        """
        try:
            r = requests.get(
                config.OLLAMA_URL.replace("/api/generate", "/api/tags"),
                timeout=3,
            )
            self._ollama_available = r.ok
        except Exception:
            self._ollama_available = False

        logger.info(f"Ollama available: {self._ollama_available}")
        return self._ollama_available

    def parse(self, text: str) -> dict:
        """
        Parse a natural-language command string into an action dict.

        Args:
            text: Raw command string (from voice or text input)

        Returns:
            dict with keys "action" and "params"
        """
        text = text.strip()
        if not text:
            return {"action": "unknown", "params": {"raw": ""}}

        # Try AI parser first (if available)
        if self._ollama_available is not False:
            result = self._parse_with_ai(text)
            if result:
                logger.info(f"AI parsed '{text}' → {result}")
                return result

        # Fallback to keyword rules
        result = self._parse_with_rules(text)
        logger.info(f"Rule-parsed '{text}' → {result}")
        return result

    def _parse_with_ai(self, text: str) -> Optional[dict]:
        """Send command to Ollama for parsing."""
        try:
            response = requests.post(
                config.OLLAMA_URL,
                json={
                    "model": config.OLLAMA_MAIN_MODEL,
                    "system": self.SYSTEM_PROMPT,
                    "prompt": f"Command: {text}",
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": 256},
                },
                timeout=15,  # Shorter timeout for command parsing (must be snappy)
            )

            if response.ok:
                self._ollama_available = True
                raw = response.json().get("response", "").strip()
                # Clean markdown fences if present
                raw = re.sub(r"```(?:json)?|```", "", raw).strip()
                parsed = json.loads(raw)
                # Validate structure
                if "action" in parsed:
                    return parsed

        except requests.exceptions.ConnectionError:
            logger.debug("Ollama unreachable — switching to rule-based")
            self._ollama_available = False
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"AI parse failed for '{text}': {e}")
        except Exception as e:
            logger.warning(f"Command parser error: {e}")

        return None

    def _parse_with_rules(self, text: str) -> dict:
        """Keyword-regex fallback parser."""
        for pattern, action, base_params in self.KEYWORD_RULES:
            if pattern.search(text):
                # Handle special cases that need parameter extraction
                if action == "takeoff":
                    m = re.search(r"(\d+)\s*(?:m|meter|meters)?", text)
                    alt = float(m.group(1)) if m else config.PATROL_ALTITUDE_M
                    return {"action": "takeoff", "params": {"altitude": alt}}

                if action == "set_gimbal":
                    # Extract direction → angle mapping
                    if re.search(r"down|nadir", text, re.I):
                        pitch = config.GIMBAL_TILT_NADIR
                    elif re.search(r"forward|front", text, re.I):
                        pitch = config.GIMBAL_TILT_FORWARD
                    else:
                        pitch = config.GIMBAL_TILT_DEFAULT
                    return {"action": "set_gimbal", "params": {"pitch": pitch}}

                if base_params is not None:
                    return {"action": action, "params": dict(base_params)}

        # Check for "scout [direction]" pattern
        scout_match = re.search(r"\bscout\b\s*(north|south|east|west|road|trail|area)?", text, re.I)
        if scout_match:
            direction = scout_match.group(1) or "ahead"
            return {"action": "scout", "params": {"direction": direction, "start": None, "end": None}}

        # Check for "takeoff" with optional altitude
        if re.search(r"\btake\s*off\b|\blaunch\b|\barm\b", text, re.I):
            m = re.search(r"(\d+)\s*(?:m|meters?)?", text)
            alt = float(m.group(1)) if m else config.PATROL_ALTITUDE_M
            return {"action": "takeoff", "params": {"altitude": alt}}

        # No match
        return {"action": "unknown", "params": {"raw": text}}
