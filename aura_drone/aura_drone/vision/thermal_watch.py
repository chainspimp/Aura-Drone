"""
vision/thermal_watch.py — FLIR Lepton 3.5 Thermal Detection

Interfaces with FLIR Lepton 3.5 via PureThermal 2 USB board.
Performs continuous temperature analysis, detects human-temperature heat blobs,
and filters false positives (sun-heated objects, vehicle engines).

The Lepton 3.5 provides 160x120 radiometric data — each pixel encodes
temperature in units of centi-Kelvin (divide by 100 to get Kelvin).
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable, Optional

import numpy as np

import config

logger = logging.getLogger("AURA.thermal")

# pylepton is the FLIR Lepton driver for PureThermal boards
try:
    from pylepton.Lepton3 import Lepton3
    PYLEPTON_AVAILABLE = True
except ImportError:
    try:
        # Alternative: access via OpenCV V4L2 (PureThermal exposes UVC)
        import cv2
        PYLEPTON_AVAILABLE = False
        CV2_THERMAL = True
    except ImportError:
        PYLEPTON_AVAILABLE = False
        CV2_THERMAL = False
    logger.warning("pylepton not available — using V4L2 fallback for thermal")

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


class ThermalWatcher:
    """
    FLIR Lepton thermal camera watcher.

    Continuously reads thermal frames, converts pixel values to Celsius,
    runs blob detection to find human-temperature heat sources,
    and fires callbacks when a match is found.

    Temperature math:
        raw_value (uint16) in centi-Kelvin (CK)
        Celsius = (raw_value / 100.0) - 273.15
    """

    def __init__(self, device_id: int = 2) -> None:
        self.device_id = device_id
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._frame_lock = threading.Lock()

        # Rolling alert buffer
        self._recent_alerts: deque = deque(maxlen=100)
        self._current_frame_celsius: Optional[np.ndarray] = None
        self._current_frame_visual: Optional[np.ndarray] = None  # False-color PNG

        # Callbacks
        self._alert_callbacks: list[Callable] = []

        # Per-blob cooldown tracking {blob_id: last_alert_time}
        self._blob_cooldowns: dict = {}

        # Camera handle
        self._cap = None  # Used for V4L2 fallback

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    def start(self) -> bool:
        """Open thermal camera and start detection thread."""
        if not self._open_camera():
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._thermal_loop, name="ThermalWatcher", daemon=True
        )
        self._thread.start()
        logger.info("Thermal watcher started")
        return True

    def stop(self) -> None:
        """Stop thermal thread and release camera."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._cap and hasattr(self._cap, 'release'):
            self._cap.release()
        logger.info("Thermal watcher stopped")

    def _open_camera(self) -> bool:
        """Open FLIR Lepton via PureThermal 2 USB."""
        if PYLEPTON_AVAILABLE:
            # pylepton uses SPI directly (Raspberry Pi / Jetson GPIO)
            logger.info("Using pylepton driver for FLIR Lepton")
            return True  # pylepton opens in loop context manager

        # Fallback: PureThermal 2 also exposes UVC (V4L2)
        if CV2_AVAILABLE:
            self._cap = cv2.VideoCapture(self.device_id)
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.THERMAL_LEPTON_WIDTH)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.THERMAL_LEPTON_HEIGHT)

            if not self._cap.isOpened():
                logger.error(f"Thermal camera not found at device {self.device_id}")
                return False

            logger.info(f"Thermal camera opened via V4L2 (device {self.device_id})")
            return True

        logger.error("No thermal camera driver available")
        return False

    # ──────────────────────────────────────────
    # Detection Loop
    # ──────────────────────────────────────────

    def _thermal_loop(self) -> None:
        """Continuous thermal frame capture and analysis."""
        if PYLEPTON_AVAILABLE:
            self._thermal_loop_pylepton()
        else:
            self._thermal_loop_v4l2()

    def _thermal_loop_pylepton(self) -> None:
        """Thermal loop using pylepton driver (radiometric raw data)."""
        buf = np.zeros((config.THERMAL_LEPTON_HEIGHT, config.THERMAL_LEPTON_WIDTH), dtype=np.uint16)

        try:
            with Lepton3() as lep:
                while self._running:
                    lep.capture(buf)
                    # Convert centi-Kelvin to Celsius
                    celsius = (buf.astype(np.float32) / 100.0) - 273.15
                    self._process_thermal_frame(celsius)
                    time.sleep(0.1)  # Lepton 3.5 = 8.7 Hz, so 0.1s poll is fine
        except Exception as e:
            logger.error(f"pylepton capture error: {e}")

    def _thermal_loop_v4l2(self) -> None:
        """Thermal loop using V4L2 (UVC mode — less precise temperature data)."""
        while self._running:
            if self._cap is None:
                break

            ret, frame = self._cap.read()
            if not ret or frame is None:
                time.sleep(0.1)
                continue

            # UVC mode returns normalized Y16 or Y8 grayscale
            # We can calibrate against known temperature targets, but for now
            # use relative values scaled to expected human temp range
            if len(frame.shape) == 3:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if CV2_AVAILABLE else frame[:,:,0]
            else:
                gray = frame

            # Scale 8-bit values to rough temperature range
            # This is uncalibrated — for calibrated data use pylepton on Jetson GPIO
            min_raw, max_raw = float(gray.min()), float(gray.max())
            if max_raw > min_raw:
                # Map to [15°C, 60°C] range (typical outdoor scene)
                celsius = 15.0 + (gray.astype(np.float32) - min_raw) / (max_raw - min_raw) * 45.0
            else:
                celsius = np.full(gray.shape, 20.0, dtype=np.float32)

            self._process_thermal_frame(celsius)
            time.sleep(0.1)

    def _process_thermal_frame(self, celsius: np.ndarray) -> None:
        """
        Analyze a thermal frame for human-temperature blobs.

        Algorithm:
        1. Threshold pixels to human temp range
        2. Morphological close to merge nearby hot pixels
        3. Find contours (blobs)
        4. Filter by minimum size and temperature stats
        5. Fire alert callbacks for valid human detections
        """
        # Update cached frame
        visual = self._frame_to_false_color(celsius)
        with self._frame_lock:
            self._current_frame_celsius = celsius.copy()
            self._current_frame_visual = visual

        # Threshold: pixels in human body temp range
        human_mask = (
            (celsius >= config.HUMAN_TEMP_MIN_C) &
            (celsius <= config.HUMAN_TEMP_MAX_C)
        ).astype(np.uint8) * 255

        # Morphological operations to merge nearby hot pixels
        if CV2_AVAILABLE:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            human_mask = cv2.morphologyEx(human_mask, cv2.MORPH_CLOSE, kernel)

            # Find blobs
            contours, _ = cv2.findContours(
                human_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            for contour in contours:
                blob_pixels = cv2.contourArea(contour)
                if blob_pixels < config.THERMAL_BLOB_MIN_PIXELS:
                    continue

                # Temperature statistics for this blob
                mask = np.zeros_like(human_mask)
                cv2.drawContours(mask, [contour], -1, 255, -1)
                blob_temps = celsius[mask > 0]
                max_temp = float(blob_temps.max())
                mean_temp = float(blob_temps.mean())

                # Additional filter: reject blobs with too-uniform temperature
                # (sun-heated flat surfaces are uniform; humans have variation)
                temp_std = float(blob_temps.std())
                if temp_std < 0.3:  # Very uniform — likely not a person
                    continue

                self._fire_alert(blob_pixels, max_temp, mean_temp, contour)
        else:
            # Fallback: simple pixel count without contour analysis
            hot_pixels = int(human_mask.sum() / 255)
            if hot_pixels >= config.THERMAL_BLOB_MIN_PIXELS:
                max_temp = float(celsius[human_mask > 0].max()) if hot_pixels > 0 else 0
                self._fire_alert(hot_pixels, max_temp, max_temp, None)

    def _fire_alert(
        self,
        blob_pixels: float,
        max_temp_c: float,
        mean_temp_c: float,
        contour,
    ) -> None:
        """
        Fire an alert for a detected human-temperature blob.
        Respects per-detection cooldown to avoid spam.
        """
        # Simple blob ID based on rounded temperature (very basic de-dup)
        blob_id = f"{max_temp_c:.1f}"
        now = time.time()

        last_alert = self._blob_cooldowns.get(blob_id, 0)
        if now - last_alert < config.THERMAL_ALERT_COOLDOWN_S:
            return

        self._blob_cooldowns[blob_id] = now

        alert = {
            "timestamp": now,
            "blob_pixels": int(blob_pixels),
            "max_temp_c": max_temp_c,
            "mean_temp_c": mean_temp_c,
            "datetime": datetime.now().isoformat(),
        }

        with self._lock:
            self._recent_alerts.append(alert)

        logger.warning(
            f"Thermal alert: {blob_pixels:.0f}px blob @ "
            f"max={max_temp_c:.1f}°C mean={mean_temp_c:.1f}°C"
        )

        for cb in self._alert_callbacks:
            try:
                cb(alert)
            except Exception as e:
                logger.error(f"Thermal alert callback error: {e}")

    # ──────────────────────────────────────────
    # Visualization
    # ──────────────────────────────────────────

    def _frame_to_false_color(self, celsius: np.ndarray) -> Optional[np.ndarray]:
        """
        Convert celsius array to a false-color image for GUI display.
        Uses COLORMAP_INFERNO: black=cold, red=warm, yellow=hot.
        """
        if not CV2_AVAILABLE:
            return None

        # Normalize to 0-255 for display
        min_c = celsius.min()
        max_c = celsius.max()
        if max_c > min_c:
            normalized = ((celsius - min_c) / (max_c - min_c) * 255).astype(np.uint8)
        else:
            normalized = np.zeros_like(celsius, dtype=np.uint8)

        colored = cv2.applyColorMap(normalized, cv2.COLORMAP_INFERNO)
        # Upscale for visibility
        return cv2.resize(
            colored,
            (config.THERMAL_LEPTON_WIDTH * 4, config.THERMAL_LEPTON_HEIGHT * 4),
            interpolation=cv2.INTER_NEAREST
        )

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def register_callback(self, callback: Callable[[dict], None]) -> None:
        """Register alert callback — receives alert dicts on human detection."""
        self._alert_callbacks.append(callback)

    def get_recent_alerts(self, window_s: float = 5.0) -> list[dict]:
        """Return thermal alerts from the last window_s seconds."""
        cutoff = time.time() - window_s
        with self._lock:
            return [a for a in self._recent_alerts if a["timestamp"] >= cutoff]

    def get_visual_frame(self) -> Optional[np.ndarray]:
        """Return the latest false-color thermal frame for GUI display."""
        with self._frame_lock:
            return self._current_frame_visual.copy() \
                if self._current_frame_visual is not None else None

    def get_celsius_frame(self) -> Optional[np.ndarray]:
        """Return the raw temperature array (float32, degrees Celsius)."""
        with self._frame_lock:
            return self._current_frame_celsius.copy() \
                if self._current_frame_celsius is not None else None
