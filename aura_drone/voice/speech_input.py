"""
voice/speech_input.py — Vosk Offline Speech Recognition

Continuously listens for voice commands using the Vosk offline STT model.
No internet required — all inference runs locally on the Jetson.

Designed to be lightweight: the wake_listener.py keeps this in a passive
mode (low CPU) until the wake word is confirmed, then this module activates
full recognition.
"""

import json
import logging
import queue
import threading
from typing import Callable, Optional

import config

logger = logging.getLogger("AURA.speech")

try:
    import vosk
    import pyaudio
    VOSK_AVAILABLE = True
except ImportError:
    logger.warning("vosk or pyaudio not installed — voice input disabled")
    VOSK_AVAILABLE = False


class SpeechInput:
    """
    Continuous Vosk-based speech-to-text in a background thread.

    Two modes:
    - Passive: Only processing partial results for wake word detection
    - Active: Full sentence recognition, fires callbacks on complete utterances

    The WakeListener controls the mode switch.
    """

    # Commands to watch for in active mode
    RECOGNIZED_COMMANDS = [
        "scout", "patrol", "return home", "return to home", "rth",
        "drop payload", "release payload", "drop package",
        "hover", "hold position", "stop",
        "land", "put it down",
        "take off", "takeoff", "launch",
        "situation report", "sitrep", "status report", "what do you see",
        "what can you see", "describe what you see",
        "orbit", "circle",
        "set relay", "relay mode",
        "send message", "broadcast",
    ]

    def __init__(self, model_path: str = None) -> None:
        self.model_path = model_path or config.VOSK_MODEL_PATH
        self._active = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._audio: Optional[object] = None
        self._stream = None
        self._model = None
        self._recognizer = None

        # Callbacks
        self._command_callbacks: list[Callable] = []
        self._partial_callbacks: list[Callable] = []   # For wake word detection

        # Internal audio queue
        self._audio_queue: queue.Queue = queue.Queue()

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    def start(self) -> bool:
        """Load Vosk model and start audio capture thread."""
        if not VOSK_AVAILABLE:
            logger.error("Vosk not available — voice input disabled")
            return False

        if not self._load_model():
            return False

        if not self._open_audio():
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._recognition_loop, name="SpeechInput", daemon=True
        )
        self._thread.start()
        logger.info(f"Speech input started (model: {self.model_path})")
        return True

    def stop(self) -> None:
        """Stop recognition and release audio resources."""
        self._running = False
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._audio:
            try:
                self._audio.terminate()
            except Exception:
                pass
        logger.info("Speech input stopped")

    def set_active(self, active: bool) -> None:
        """
        Toggle active command recognition mode.
        In passive mode, only partials are emitted (for wake word).
        In active mode, complete utterances fire command callbacks.
        """
        self._active = active
        logger.debug(f"Speech recognition mode: {'ACTIVE' if active else 'PASSIVE'}")

    # ──────────────────────────────────────────
    # Model + Audio Setup
    # ──────────────────────────────────────────

    def _load_model(self) -> bool:
        """Load Vosk model from disk."""
        import os
        if not os.path.exists(self.model_path):
            logger.error(
                f"Vosk model not found at '{self.model_path}'. "
                "Download from: https://alphacephei.com/vosk/models"
            )
            return False

        try:
            vosk.SetLogLevel(-1)  # Suppress Vosk verbose output
            self._model = vosk.Model(self.model_path)
            self._recognizer = vosk.KaldiRecognizer(self._model, config.AUDIO_SAMPLE_RATE)
            self._recognizer.SetWords(True)
            logger.info("Vosk model loaded")
            return True
        except Exception as e:
            logger.error(f"Vosk model load failed: {e}")
            return False

    def _open_audio(self) -> bool:
        """Open PyAudio input stream."""
        try:
            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=config.AUDIO_SAMPLE_RATE,
                input=True,
                frames_per_buffer=config.AUDIO_BLOCK_SIZE,
                stream_callback=self._audio_callback,
            )
            self._stream.start_stream()
            logger.info("Audio input stream opened")
            return True
        except Exception as e:
            logger.error(f"Audio open failed: {e}")
            return False

    def _audio_callback(self, in_data, frame_count, time_info, status) -> tuple:
        """PyAudio callback — queue audio blocks for processing thread."""
        self._audio_queue.put(bytes(in_data))
        return (None, pyaudio.paContinue)

    # ──────────────────────────────────────────
    # Recognition Loop
    # ──────────────────────────────────────────

    def _recognition_loop(self) -> None:
        """
        Main recognition loop — processes audio blocks from the queue.

        Partial results (text as you speak) are emitted to partial callbacks
        for wake word detection.

        Final results (end of utterance) are emitted to command callbacks
        when in active mode.
        """
        while self._running:
            try:
                data = self._audio_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                if self._recognizer.AcceptWaveform(data):
                    # Final result — complete utterance
                    result = json.loads(self._recognizer.Result())
                    text = result.get("text", "").strip()

                    if text:
                        logger.debug(f"STT final: '{text}'")
                        # Notify partial callbacks too (wake listener uses these)
                        for cb in self._partial_callbacks:
                            try:
                                cb(text)
                            except Exception as e:
                                logger.error(f"Partial callback error: {e}")

                        # Only fire command callbacks in active mode
                        if self._active:
                            for cb in self._command_callbacks:
                                try:
                                    cb(text)
                                except Exception as e:
                                    logger.error(f"Command callback error: {e}")
                            # Auto-deactivate after receiving one command
                            # (re-activated by next wake word)
                            self._active = False

                else:
                    # Partial result — emit for wake word scanning
                    partial = json.loads(self._recognizer.PartialResult())
                    partial_text = partial.get("partial", "").strip()

                    if partial_text:
                        for cb in self._partial_callbacks:
                            try:
                                cb(partial_text)
                            except Exception as e:
                                logger.error(f"Partial callback error: {e}")

            except Exception as e:
                logger.error(f"Recognition error: {e}")

    # ──────────────────────────────────────────
    # Callback Registration
    # ──────────────────────────────────────────

    def register_callback(self, callback: Callable[[str], None]) -> None:
        """Register callback for complete voice commands (active mode only)."""
        self._command_callbacks.append(callback)

    def register_partial_callback(self, callback: Callable[[str], None]) -> None:
        """Register callback for partial recognition results (always active)."""
        self._partial_callbacks.append(callback)
