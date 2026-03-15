"""
voice/tts_output.py — Piper TTS Spoken Alerts

Converts text to speech using Piper (offline neural TTS).
Outputs audio to the MAX98357A I2S amplifier connected speaker.

Piper generates high-quality speech locally — no internet, no API keys.
The female HFC voice model provides clear, natural-sounding alerts.

Fallback: espeak (lower quality but always available on Ubuntu).
"""

import logging
import os
import queue
import subprocess
import tempfile
import threading
from typing import Optional

import config

logger = logging.getLogger("AURA.tts")


class TTSOutput:
    """
    Text-to-speech output with priority queue.

    Speak requests are queued so they don't block callers.
    Priority levels: 0 = critical (jumps queue), 1 = normal

    Audio pipeline:
        text → Piper (neural TTS) → WAV file → aplay (ALSA)
        or
        text → espeak (fallback)
    """

    def __init__(self) -> None:
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._piper_available = False
        self._espeak_available = False
        self._sequence = 0  # For stable priority queue ordering

        self._check_tts_availability()
        self._start_worker()

    def _check_tts_availability(self) -> None:
        """Check which TTS engines are available on this system."""
        # Check Piper
        if os.path.exists(config.PIPER_PATH) and os.path.exists(config.PIPER_MODEL):
            self._piper_available = True
            logger.info(f"Piper TTS available: {config.PIPER_PATH}")
        else:
            logger.warning(
                f"Piper not found at {config.PIPER_PATH} or model at {config.PIPER_MODEL}. "
                "Falling back to espeak."
            )

        # Check espeak
        try:
            result = subprocess.run(
                ["espeak", "--version"],
                capture_output=True, timeout=3
            )
            self._espeak_available = result.returncode == 0
            if self._espeak_available:
                logger.info("espeak fallback TTS available")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.warning("espeak not found — TTS may be silent")

    def _start_worker(self) -> None:
        """Start background TTS worker thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop, name="TTSWorker", daemon=True
        )
        self._thread.start()

    def _worker_loop(self) -> None:
        """Process TTS requests from queue sequentially."""
        while self._running:
            try:
                priority, seq, text = self._queue.get(timeout=1.0)
                self._synthesize_and_play(text)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"TTS worker error: {e}")

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def speak(self, text: str, priority: int = 1) -> None:
        """
        Queue text for speech synthesis.

        Args:
            text: Text to speak aloud
            priority: 0 = urgent (interrupts queue), 1 = normal
        """
        if not text or not text.strip():
            return

        # Truncate very long text
        text = text[:500].strip()

        # Add to priority queue (lower number = higher priority)
        # Sequence number ensures FIFO within same priority level
        self._sequence += 1
        self._queue.put((priority, self._sequence, text))
        logger.debug(f"TTS queued (priority={priority}): '{text[:60]}'")

    def speak_urgent(self, text: str) -> None:
        """Speak immediately — jumps the queue."""
        self.speak(text, priority=0)

    # ──────────────────────────────────────────
    # Synthesis
    # ──────────────────────────────────────────

    def _synthesize_and_play(self, text: str) -> None:
        """Synthesize text and play audio."""
        if self._piper_available:
            self._piper_speak(text)
        elif self._espeak_available:
            self._espeak_speak(text)
        else:
            logger.warning(f"TTS: No engine available — would have said: '{text}'")

    def _piper_speak(self, text: str) -> None:
        """
        Synthesize with Piper and play via aplay.

        Piper reads from stdin, outputs WAV to stdout.
        aplay plays the WAV to the ALSA default output (I2S amp).
        """
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = wav_file.name

            # Run Piper: echo "text" | piper --model model.onnx --output_file out.wav
            piper_cmd = [
                config.PIPER_PATH,
                "--model", config.PIPER_MODEL,
                "--output_file", wav_path,
            ]

            proc = subprocess.run(
                piper_cmd,
                input=text.encode(),
                capture_output=True,
                timeout=30,
            )

            if proc.returncode != 0:
                logger.error(f"Piper failed: {proc.stderr.decode()}")
                self._espeak_speak(text)  # Fall through to espeak
                return

            # Play the WAV file
            subprocess.run(
                ["aplay", "-q", wav_path],
                timeout=60,
            )

        except subprocess.TimeoutExpired:
            logger.warning("TTS synthesis timed out")
        except Exception as e:
            logger.error(f"Piper TTS error: {e}")
            try:
                self._espeak_speak(text)
            except Exception:
                pass
        finally:
            # Clean up temp file
            try:
                os.unlink(wav_path)
            except Exception:
                pass

    def _espeak_speak(self, text: str) -> None:
        """
        Speak using espeak (lower quality but always available).
        Runs synchronously — aplay not needed since espeak handles audio.
        """
        try:
            subprocess.run(
                ["espeak", "-v", "en+f4", "-s", "160", text],
                timeout=30,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            logger.warning("espeak timed out")
        except FileNotFoundError:
            logger.error("espeak not found")
        except Exception as e:
            logger.error(f"espeak error: {e}")

    def stop(self) -> None:
        """Stop TTS worker thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
