"""
vision/yolo_watch.py — YOLOv8 Real-Time Object Detection

Runs YOLOv8n inference on the main ArduCam HQ feed in a background thread.
Detects threat classes (person, vehicle) and resource classes (backpack, boat).
Publishes detection events via callbacks; subscribers never need to touch OpenCV.

TensorRT acceleration is automatically used if an .engine model file is present,
falling back to PyTorch on CPU/CUDA otherwise.
"""

import logging
import os
import threading
import time
from collections import deque
from typing import Callable, Optional

import cv2
import numpy as np

import config

logger = logging.getLogger("AURA.yolo")

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    logger.warning("ultralytics not installed — YOLO detection disabled")
    ULTRALYTICS_AVAILABLE = False


class YOLOWatcher:
    """
    Continuous YOLO detection thread with subscriber callbacks.

    Usage:
        watcher = YOLOWatcher(camera_id=0)
        watcher.register_callback(my_handler)
        watcher.start()
        ...
        watcher.stop()

    Callbacks receive a detection dict:
        {
            "class": "person",
            "confidence": 0.87,
            "bbox": [x1, y1, x2, y2],  # pixel coordinates
            "timestamp": 1703001234.5,
        }
    """

    def __init__(self, camera_id: int = 0) -> None:
        self.camera_id = camera_id
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Ring buffer of recent detections (last 5 seconds at ~30fps)
        self._recent_detections: deque = deque(maxlen=500)
        self._current_frame: Optional[np.ndarray] = None
        self._annotated_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()

        # Subscriber lists
        self._detection_callbacks: list[Callable] = []
        self._frame_callbacks: list[Callable] = []

        # Performance tracking
        self._fps: float = 0.0
        self._frame_count: int = 0

        # Model
        self._model = None
        self._cap: Optional[cv2.VideoCapture] = None

    # ──────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────

    def start(self) -> bool:
        """Load model and start detection thread."""
        if not ULTRALYTICS_AVAILABLE:
            logger.error("Cannot start YOLO — ultralytics not installed")
            return False

        if not self._load_model():
            return False

        if not self._open_camera():
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._detection_loop, name="YOLOWatcher", daemon=True
        )
        self._thread.start()
        logger.info(f"YOLO watcher started on camera {self.camera_id}")
        return True

    def stop(self) -> None:
        """Stop detection thread and release camera."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._cap:
            self._cap.release()
        logger.info("YOLO watcher stopped")

    def _load_model(self) -> bool:
        """Load YOLO model — prefer TensorRT .engine if available."""
        model_path = config.YOLO_MODEL_PATH
        engine_path = model_path.replace(".pt", ".engine")

        # TensorRT engine is much faster on Jetson
        if os.path.exists(engine_path):
            logger.info(f"Loading TensorRT engine: {engine_path}")
            load_path = engine_path
        elif os.path.exists(model_path):
            logger.info(f"Loading PyTorch model: {model_path}")
            load_path = model_path
        else:
            # Auto-download YOLOv8n on first run
            logger.info("Downloading YOLOv8n model (first run)")
            load_path = "yolov8n.pt"

        try:
            self._model = YOLO(load_path)
            logger.info("YOLO model loaded")
            return True
        except Exception as e:
            logger.error(f"YOLO model load failed: {e}")
            return False

    def _open_camera(self) -> bool:
        """Open the main camera with appropriate settings for Jetson."""
        # On Jetson, CSI cameras use GStreamer pipeline
        # USB cameras use standard V4L2 index
        self._cap = cv2.VideoCapture(self.camera_id)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.MAIN_CAM_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.MAIN_CAM_HEIGHT)
        self._cap.set(cv2.CAP_PROP_FPS, config.MAIN_CAM_FPS)

        if not self._cap.isOpened():
            logger.error(f"Failed to open camera {self.camera_id}")
            return False

        ret, frame = self._cap.read()
        if not ret or frame is None:
            logger.error(f"Camera {self.camera_id} opened but no frames received")
            return False

        logger.info(
            f"Camera {self.camera_id} opened: "
            f"{int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
            f"{int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} @ "
            f"{self._cap.get(cv2.CAP_PROP_FPS):.0f}fps"
        )
        return True

    # ──────────────────────────────────────────
    # Detection Loop
    # ──────────────────────────────────────────

    def _detection_loop(self) -> None:
        """Main inference loop — runs in background thread."""
        fps_start = time.time()
        fps_frames = 0

        while self._running:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                logger.warning("Camera read failed — retrying")
                time.sleep(0.1)
                continue

            # Update raw frame for frame callbacks (face_id etc.)
            with self._frame_lock:
                self._current_frame = frame.copy()

            # Run YOLO inference
            try:
                results = self._model(
                    frame,
                    imgsz=config.YOLO_INFERENCE_SIZE,
                    conf=config.YOLO_CONFIDENCE_THRESHOLD,
                    iou=config.YOLO_IOU_THRESHOLD,
                    verbose=False,
                )

                annotated = results[0].plot()  # Frame with bounding boxes drawn
                with self._frame_lock:
                    self._annotated_frame = annotated

                # Process detections
                self._process_results(results[0])

            except Exception as e:
                logger.error(f"YOLO inference error: {e}")

            # Notify frame subscribers (face_id runs on every Nth frame)
            self._frame_count += 1
            if self._frame_count % config.FACE_ID_FRAME_SKIP == 0:
                with self._frame_lock:
                    fc = self._current_frame
                for cb in self._frame_callbacks:
                    try:
                        cb(fc)
                    except Exception as e:
                        logger.error(f"Frame callback error: {e}")

            # FPS tracking
            fps_frames += 1
            elapsed = time.time() - fps_start
            if elapsed >= 5.0:
                self._fps = fps_frames / elapsed
                fps_start = time.time()
                fps_frames = 0
                logger.debug(f"YOLO FPS: {self._fps:.1f}")

    def _process_results(self, result) -> None:
        """
        Extract detections from YOLO result, filter to alert classes,
        and dispatch to callbacks.
        """
        if result.boxes is None:
            return

        now = time.time()
        boxes = result.boxes

        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item())
            cls_name = result.names[cls_id]
            confidence = float(boxes.conf[i].item())
            bbox = boxes.xyxy[i].tolist()  # [x1, y1, x2, y2]

            # Only process classes we care about
            if cls_name not in config.YOLO_ALERT_CLASSES:
                continue

            detection = {
                "class": cls_name,
                "confidence": confidence,
                "bbox": bbox,
                "timestamp": now,
            }

            # Add to rolling buffer
            with self._lock:
                self._recent_detections.append(detection)

            # Notify subscribers
            for cb in self._detection_callbacks:
                try:
                    cb(detection)
                except Exception as e:
                    logger.error(f"Detection callback error: {e}")

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def register_callback(self, callback: Callable[[dict], None]) -> None:
        """Register a function to receive detection events."""
        self._detection_callbacks.append(callback)

    def register_frame_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Register a function to receive raw frames (for face_id, map_builder, etc.)."""
        self._frame_callbacks.append(callback)

    def get_current_frame(self) -> Optional[np.ndarray]:
        """Return the latest raw camera frame (thread-safe copy)."""
        with self._frame_lock:
            return self._current_frame.copy() if self._current_frame is not None else None

    def get_annotated_frame(self) -> Optional[np.ndarray]:
        """Return latest frame with YOLO bounding boxes drawn."""
        with self._frame_lock:
            return self._annotated_frame.copy() if self._annotated_frame is not None else None

    def get_recent_detections(self, window_s: float = 5.0) -> list[dict]:
        """
        Return all detections within the last window_s seconds.

        Args:
            window_s: Time window in seconds

        Returns:
            List of detection dicts (newest first)
        """
        cutoff = time.time() - window_s
        with self._lock:
            return [d for d in self._recent_detections if d["timestamp"] >= cutoff]

    def get_fps(self) -> float:
        """Return measured inference FPS."""
        return self._fps
