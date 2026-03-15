"""
ai/situation_report.py — Situation Report Generator

Compiles current sensor data into human-readable situation reports.
Uses Ollama for natural language generation, with structured fallbacks.
Also handles "what do you see?" visual queries via the vision model.
"""

import base64
import json
import logging
import time
from datetime import datetime
from typing import Optional

import numpy as np
import requests

import config

logger = logging.getLogger("AURA.sitrep")


class SituationReporter:
    """
    Generates operator-facing situation reports from multi-sensor data.

    Two report types:
    1. Tactical SITREP: Combines telemetry + detections + thermal into text summary
    2. Visual description: Sends a camera frame to the vision model for scene description
    """

    def generate(
        self,
        telemetry: dict,
        detections: list[dict],
        thermal_alerts: list[dict],
    ) -> str:
        """
        Generate a concise tactical situation report.

        Args:
            telemetry: Drone state dict
            detections: Recent YOLO detections
            thermal_alerts: Recent thermal alerts

        Returns:
            Human-readable situation report string (2-5 sentences)
        """
        # Build context summary
        bat = telemetry.get("battery_percent", "?")
        alt = telemetry.get("altitude_m", 0)
        lat = telemetry.get("latitude", 0)
        lon = telemetry.get("longitude", 0)
        mode = telemetry.get("mode", "?")

        # Summarize detections
        class_counts: dict = {}
        for d in detections:
            cls = d.get("class", "unknown")
            class_counts[cls] = class_counts.get(cls, 0) + 1

        persons = class_counts.get("person", 0)
        vehicles = sum(class_counts.get(c, 0) for c in ["car", "truck", "motorcycle"])
        thermal_count = len(thermal_alerts)

        # Try AI generation
        ai_report = self._generate_with_ai(
            bat, alt, lat, lon, mode, persons, vehicles, thermal_count, class_counts
        )
        if ai_report:
            return ai_report

        # Structured fallback
        return self._build_structured_report(
            bat, alt, lat, lon, mode, persons, vehicles, thermal_count, class_counts
        )

    def describe_frame(self, frame: np.ndarray) -> str:
        """
        Send a camera frame to the vision AI model and get a scene description.

        Args:
            frame: BGR numpy array from OpenCV

        Returns:
            Natural language scene description
        """
        if frame is None:
            return "No camera frame available."

        try:
            import cv2

            # Encode frame to JPEG for transmission
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            img_b64 = base64.b64encode(buf.tobytes()).decode("utf-8")

            # Use vision model for frame analysis
            response = requests.post(
                config.OLLAMA_URL,
                json={
                    "model": config.OLLAMA_VISION_MODEL,
                    "prompt": (
                        "You are an aerial reconnaissance AI. "
                        "Describe what you see in this drone camera image. "
                        "Focus on: people, vehicles, structures, terrain, and anything "
                        "that could be a threat or survival resource. Be brief and tactical."
                    ),
                    "images": [img_b64],
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 300},
                },
                timeout=config.OLLAMA_TIMEOUT_S,
            )

            if response.ok:
                return response.json().get("response", "").strip()

        except ImportError:
            return "Visual analysis unavailable (OpenCV not installed)"
        except requests.exceptions.ConnectionError:
            return "Visual AI offline — describe what you observe manually"
        except Exception as e:
            logger.error(f"Frame description error: {e}")

        return "Visual analysis failed — check camera and AI status"

    def _generate_with_ai(
        self,
        bat, alt, lat, lon, mode,
        persons, vehicles, thermal_count,
        class_counts,
    ) -> Optional[str]:
        """Generate SITREP text via Ollama."""
        try:
            prompt = (
                "Generate a 2-3 sentence tactical situation report for a survival drone operator.\n\n"
                f"Battery: {bat}%\n"
                f"Altitude: {alt:.1f}m AGL\n"
                f"Position: ({lat:.5f}, {lon:.5f})\n"
                f"Mode: {mode}\n"
                f"Persons detected: {persons}\n"
                f"Vehicles detected: {vehicles}\n"
                f"Thermal contacts: {thermal_count}\n"
                f"Other detections: {json.dumps(class_counts)}\n\n"
                "Write directly as AURA speaking to the operator. "
                "Start with threat level (CLEAR/LOW/MEDIUM/HIGH). "
                "Be concise — operator is in the field."
            )

            response = requests.post(
                config.OLLAMA_URL,
                json={
                    "model": config.OLLAMA_MAIN_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 200},
                },
                timeout=30,
            )

            if response.ok:
                return response.json().get("response", "").strip()

        except Exception:
            pass
        return None

    @staticmethod
    def _build_structured_report(
        bat, alt, lat, lon, mode,
        persons, vehicles, thermal_count,
        class_counts,
    ) -> str:
        """Structured fallback SITREP without AI."""
        parts = []

        # Threat level
        if persons > 2 or thermal_count > 2:
            level = "HIGH"
        elif persons > 0 or thermal_count > 0:
            level = "MEDIUM"
        elif vehicles > 0:
            level = "LOW"
        else:
            level = "CLEAR"

        parts.append(f"THREAT LEVEL: {level}.")
        parts.append(f"Battery {bat}%, altitude {alt:.0f}m, mode {mode}.")

        if persons > 0:
            parts.append(f"Visual: {persons} person(s) detected.")
        if vehicles > 0:
            parts.append(f"Vehicles: {vehicles} detected.")
        if thermal_count > 0:
            parts.append(f"Thermal: {thermal_count} human heat signature(s).")

        other = {k: v for k, v in class_counts.items() if k not in ["person", "car", "truck", "motorcycle"]}
        if other:
            items = ", ".join(f"{v} {k}" for k, v in other.items())
            parts.append(f"Also detected: {items}.")

        return " ".join(parts)
