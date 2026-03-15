"""
ui/alert_manager.py — Alert Queue Manager

Central alert bus. All subsystems deposit alerts here.
The GUI polls this for display; the TTS system is notified for audio alerts.
Thread-safe — multiple subsystems can add alerts concurrently.
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger("AURA.alerts")

# Level priority ordering (for filtering)
LEVEL_PRIORITY = {
    "critical": 4,
    "error": 3,
    "warning": 2,
    "info": 1,
    "debug": 0,
}

# Level display colors (used by GUI)
LEVEL_COLORS = {
    "critical": "#FF0000",
    "error": "#FF6600",
    "warning": "#FFAA00",
    "info": "#00CC44",
    "debug": "#888888",
}


class Alert:
    """Single alert entry."""

    def __init__(self, title: str, message: str, level: str = "info") -> None:
        self.title = title
        self.message = message
        self.level = level.lower()
        self.timestamp = time.time()
        self.datetime_str = datetime.now().strftime("%H:%M:%S")
        self.acknowledged = False

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "message": self.message,
            "level": self.level,
            "timestamp": self.timestamp,
            "datetime": self.datetime_str,
            "acknowledged": self.acknowledged,
        }

    def format_display(self) -> str:
        """Format as a single display line for GUI log."""
        return f"[{self.datetime_str}] [{self.level.upper():8s}] {self.title}: {self.message}"


class AlertManager:
    """
    Thread-safe alert queue with subscriber callbacks.

    Alerts are stored in a bounded ring buffer. The GUI reads from this
    periodically; external subscribers (TTS, logging) receive callbacks
    immediately on new alert.

    Usage:
        alerts = AlertManager()
        alerts.register_callback(tts.speak_urgent)   # Audio alerts
        alerts.add_alert("THREAT", "Person detected", level="warning")
    """

    def __init__(self, max_alerts: int = None) -> None:
        self._alerts: deque = deque(maxlen=max_alerts or 1000)
        self._lock = threading.Lock()
        self._callbacks: list[Callable] = []
        self._unread_count = 0

    def add_alert(self, title: str, message: str, level: str = "info") -> Alert:
        """
        Add an alert to the queue.

        Args:
            title: Short alert category label
            message: Full alert description
            level: "critical" | "error" | "warning" | "info" | "debug"

        Returns:
            The Alert object that was added
        """
        alert = Alert(title=title, message=message, level=level)

        with self._lock:
            self._alerts.append(alert)
            self._unread_count += 1

        # Log to Python logging system
        log_level = {
            "critical": logging.CRITICAL,
            "error": logging.ERROR,
            "warning": logging.WARNING,
        }.get(level, logging.INFO)

        logger.log(log_level, f"[ALERT] {title}: {message}")

        # Notify subscribers
        for cb in self._callbacks:
            try:
                cb(alert)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")

        return alert

    def get_all(self, min_level: str = "debug") -> list[Alert]:
        """
        Return all alerts at or above the specified level.

        Args:
            min_level: Minimum level to include ("debug" returns everything)

        Returns:
            List of Alert objects (newest last)
        """
        min_priority = LEVEL_PRIORITY.get(min_level, 0)
        with self._lock:
            return [
                a for a in self._alerts
                if LEVEL_PRIORITY.get(a.level, 0) >= min_priority
            ]

    def get_recent(self, count: int = 50, min_level: str = "debug") -> list[Alert]:
        """Return the N most recent alerts."""
        all_alerts = self.get_all(min_level=min_level)
        return all_alerts[-count:]

    def get_unread_count(self) -> int:
        """Return count of unacknowledged alerts."""
        return self._unread_count

    def acknowledge_all(self) -> None:
        """Mark all alerts as read."""
        with self._lock:
            for alert in self._alerts:
                alert.acknowledged = True
            self._unread_count = 0

    def clear(self) -> None:
        """Clear all alerts from the queue."""
        with self._lock:
            self._alerts.clear()
            self._unread_count = 0

    def register_callback(self, callback: Callable[[Alert], None]) -> None:
        """
        Register a callback that fires on every new alert.

        Useful for:
        - TTS: speak critical alerts aloud
        - GUI: immediate visual notification
        - Remote relay: forward critical alerts via LoRa
        """
        self._callbacks.append(callback)

    def get_color(self, level: str) -> str:
        """Return the display color hex string for a given alert level."""
        return LEVEL_COLORS.get(level, LEVEL_COLORS["info"])
