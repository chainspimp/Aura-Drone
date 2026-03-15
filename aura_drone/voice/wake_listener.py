"""
voice/wake_listener.py — Always-On Wake Word Listener

Monitors the partial speech recognition stream for the wake phrase "Hey AURA"
(or whatever WAKE_WORD is configured). When detected, fires the on_wake callback
to activate full command recognition.

This module keeps CPU usage minimal — it only scans partial Vosk results
rather than running a separate wake word detection model.

For production deployments with stricter power budgets, consider replacing
with Porcupine wake word engine (offline, optimized for embedded).
"""

import logging
import time
from typing import Callable, Optional

import config

logger = logging.getLogger("AURA.wake")


class WakeListener:
    """
    Lightweight wake word detector that hooks into SpeechInput's partial results.

    Wake word detection algorithm:
    1. Subscribe to partial recognition results (real-time as user speaks)
    2. Normalize: lowercase, strip punctuation
    3. Check if WAKE_WORD tokens appear in the partial result
    4. Enforce cooldown to prevent double-firing

    This is not a dedicated wake word model — it will have more false positives
    than a purpose-built solution like Porcupine. In a survival context,
    false positives are preferable to missed activations.
    """

    WAKE_COOLDOWN_S: float = 3.0  # Minimum seconds between wake activations

    def __init__(
        self,
        speech_input,  # SpeechInput instance
        wake_word: str = None,
        on_wake: Callable = None,
    ) -> None:
        self.speech_input = speech_input
        self.wake_word = (wake_word or config.WAKE_WORD).lower().strip()
        self.on_wake = on_wake
        self._last_wake_time: float = 0.0
        self._wake_tokens = self.wake_word.split()
        self._running = False

    def start(self) -> bool:
        """Register partial speech callback and start listening."""
        if not self.speech_input:
            logger.error("No SpeechInput instance — wake listener cannot start")
            return False

        # Register as a partial result subscriber
        self.speech_input.register_partial_callback(self._on_partial)
        self._running = True
        logger.info(f"Wake listener active — phrase: '{self.wake_word}'")
        return True

    def stop(self) -> None:
        """Deactivate wake listener."""
        self._running = False
        logger.info("Wake listener stopped")

    def _on_partial(self, text: str) -> None:
        """
        Called on every partial STT result.
        Check if the wake word appears in the partial text.
        """
        if not self._running:
            return

        # Normalize text
        normalized = text.lower().strip()

        # Check for wake word match
        if self._matches_wake_word(normalized):
            now = time.time()
            if now - self._last_wake_time >= self.WAKE_COOLDOWN_S:
                self._last_wake_time = now
                logger.info(f"Wake word detected: '{text}'")
                self._fire_wake()

    def _matches_wake_word(self, text: str) -> bool:
        """
        Check if the wake word tokens appear in the recognized text.

        Uses token-based matching rather than exact string match to handle
        variations in recognition output ("hey aura", "hey, aura", "hay aura").
        """
        words = text.split()

        # Simple case: wake word is a substring
        if self.wake_word in text:
            return True

        # Token overlap: check if all wake tokens appear in order
        wake_idx = 0
        for word in words:
            if wake_idx < len(self._wake_tokens):
                # Allow for one-character edit distance (handles "aura"/"auria")
                if self._fuzzy_match(word, self._wake_tokens[wake_idx]):
                    wake_idx += 1
            if wake_idx == len(self._wake_tokens):
                return True

        # Also check for just "aura" alone — operator under stress may skip "hey"
        return "aura" in text

    @staticmethod
    def _fuzzy_match(word: str, target: str, max_distance: int = 1) -> bool:
        """Simple Levenshtein distance check for single-word fuzzy matching."""
        if word == target:
            return True
        if abs(len(word) - len(target)) > max_distance:
            return False

        # Count character differences (simplified edit distance)
        differences = sum(1 for a, b in zip(word.ljust(len(target)), target.ljust(len(word))) if a != b)
        return differences <= max_distance

    def _fire_wake(self) -> None:
        """Trigger wake word handler and activate full speech recognition."""
        # Activate full command recognition in SpeechInput
        if self.speech_input:
            self.speech_input.set_active(True)

        # Notify main system
        if self.on_wake:
            try:
                self.on_wake()
            except Exception as e:
                logger.error(f"Wake word callback error: {e}")
