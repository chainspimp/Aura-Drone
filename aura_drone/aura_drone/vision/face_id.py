"""
vision/face_id.py — Face Recognition (Known Group vs Unknown Alert)

Loads known safe faces from the known_faces/ directory on startup.
Runs face_recognition on camera frames to identify individuals.
Alerts on unknown faces. Maintains a seen-faces log with GPS timestamps.
"""

import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

import numpy as np

import config

logger = logging.getLogger("AURA.face_id")

try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    logger.warning("face_recognition not installed — face ID disabled")
    FACE_RECOGNITION_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


class FaceIdentifier:
    """
    Real-time face recognition against a database of known safe individuals.

    The known_faces/ directory structure:
        known_faces/
            Alice_Smith.jpg
            Bob_Jones.jpg
            ...

    Filename (without extension) becomes the person's name.
    Multiple images per person are supported — prefix with name:
        Alice_Smith_1.jpg
        Alice_Smith_2.jpg

    On an unknown face: fires alert callback with cropped face image.
    On a known face: logs "confirmed safe" entry (no alert).
    """

    def __init__(self, known_faces_dir: str = "known_faces/") -> None:
        self.known_faces_dir = known_faces_dir
        self._lock = threading.Lock()

        # Loaded face data
        self._known_encodings: list[np.ndarray] = []
        self._known_names: list[str] = []

        # Track recently seen faces to enforce cooldown (prevent alert spam)
        # Format: {face_hash: last_alert_time}
        self._seen_unknowns: dict = {}
        self._seen_known: dict = {}  # {name: last_seen_time}

        # Alert callbacks
        self._alert_callbacks: list[Callable] = []

        # Load faces on init
        self._load_known_faces()

    # ──────────────────────────────────────────
    # Face Database Management
    # ──────────────────────────────────────────

    def _load_known_faces(self) -> None:
        """Scan known_faces/ directory and encode all face images."""
        if not FACE_RECOGNITION_AVAILABLE:
            return

        os.makedirs(self.known_faces_dir, exist_ok=True)
        loaded = 0

        for filename in os.listdir(self.known_faces_dir):
            if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                continue

            filepath = os.path.join(self.known_faces_dir, filename)
            # Extract name from filename (strip extension and trailing _N number)
            name_raw = os.path.splitext(filename)[0]
            # Handle "Alice_Smith_1" → "Alice Smith"
            parts = name_raw.split("_")
            if parts[-1].isdigit():
                parts = parts[:-1]
            name = " ".join(parts)

            try:
                image = face_recognition.load_image_file(filepath)
                encodings = face_recognition.face_encodings(image)

                if encodings:
                    self._known_encodings.append(encodings[0])
                    self._known_names.append(name)
                    loaded += 1
                    logger.info(f"Loaded face: {name}")
                else:
                    logger.warning(f"No face found in {filename} — skipping")

            except Exception as e:
                logger.error(f"Failed to load face {filename}: {e}")

        logger.info(f"Face database loaded: {loaded} known individuals")

    def add_face(self, name: str, image_path: str) -> bool:
        """
        Add a new person to the known safe group at runtime.

        Args:
            name: Person's name
            image_path: Path to a clear face image

        Returns:
            True if face was successfully encoded and added
        """
        if not FACE_RECOGNITION_AVAILABLE:
            return False

        try:
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)

            if not encodings:
                logger.error(f"No face found in {image_path}")
                return False

            with self._lock:
                self._known_encodings.append(encodings[0])
                self._known_names.append(name)

            # Save to known_faces directory for persistence
            import shutil
            safe_name = name.replace(" ", "_")
            dest = os.path.join(
                self.known_faces_dir,
                f"{safe_name}_{int(time.time())}.jpg"
            )
            shutil.copy2(image_path, dest)

            logger.info(f"Added {name} to known faces")
            return True

        except Exception as e:
            logger.error(f"add_face failed for {name}: {e}")
            return False

    # ──────────────────────────────────────────
    # Frame Processing
    # ──────────────────────────────────────────

    def process_frame(self, frame: np.ndarray) -> None:
        """
        Process a camera frame for face recognition.
        Called by YOLO watcher's frame callback (every FACE_ID_FRAME_SKIP frames).

        Args:
            frame: BGR frame from OpenCV camera capture
        """
        if not FACE_RECOGNITION_AVAILABLE or frame is None:
            return

        try:
            # Resize to smaller resolution for faster face detection
            # face_recognition works on RGB, OpenCV uses BGR
            small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5) if CV2_AVAILABLE else frame
            rgb_small = small[:, :, ::-1]  # BGR → RGB

            # Detect face locations first (faster HOG method)
            face_locations = face_recognition.face_locations(rgb_small, model="hog")

            if not face_locations:
                return  # No faces in frame

            # Encode detected faces
            face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

            now = time.time()

            for face_encoding, face_location in zip(face_encodings, face_locations):
                name = self._match_face(face_encoding)

                if name:
                    # Known person — log but don't alert
                    self._seen_known[name] = now
                    logger.debug(f"Known person confirmed: {name}")
                else:
                    # Unknown face — alert with cooldown
                    face_hash = self._hash_encoding(face_encoding)
                    last_alert = self._seen_unknowns.get(face_hash, 0)

                    if now - last_alert >= config.FACE_UNKNOWN_ALERT_COOLDOWN_S:
                        self._seen_unknowns[face_hash] = now

                        # Crop face image (scale locations back up from 0.5x resize)
                        top, right, bottom, left = [c * 2 for c in face_location]
                        face_crop = frame[top:bottom, left:right] if CV2_AVAILABLE else None

                        self._fire_alert(face_crop, face_location)

        except Exception as e:
            logger.error(f"Face recognition error: {e}")

    def _match_face(self, encoding: np.ndarray) -> Optional[str]:
        """
        Compare encoding against known database.

        Returns:
            Name if matched, None if unknown
        """
        if not self._known_encodings:
            return None

        with self._lock:
            matches = face_recognition.compare_faces(
                self._known_encodings,
                encoding,
                tolerance=config.FACE_RECOGNITION_TOLERANCE
            )
            distances = face_recognition.face_distance(self._known_encodings, encoding)

        if any(matches):
            # Return the name with smallest distance (most confident match)
            best_idx = int(np.argmin(distances))
            if matches[best_idx]:
                return self._known_names[best_idx]

        return None

    def _fire_alert(
        self,
        face_crop: Optional[np.ndarray],
        face_location: tuple,
    ) -> None:
        """Fire alert callbacks for an unknown face detection."""
        # Save face crop image
        crop_path = None
        if face_crop is not None and CV2_AVAILABLE:
            os.makedirs(config.DETECTION_LOG_DIR, exist_ok=True)
            crop_filename = f"unknown_face_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
            crop_path = os.path.join(config.DETECTION_LOG_DIR, crop_filename)
            cv2.imwrite(crop_path, face_crop)

        alert = {
            "name": "Unknown",
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
            "face_location": face_location,
            "face_image_path": crop_path,
        }

        logger.warning(f"UNKNOWN FACE detected — image saved to {crop_path}")

        for cb in self._alert_callbacks:
            try:
                cb(alert)
            except Exception as e:
                logger.error(f"Face alert callback error: {e}")

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def register_callback(self, callback: Callable[[dict], None]) -> None:
        """Register callback for unknown face alerts."""
        self._alert_callbacks.append(callback)

    def get_known_names(self) -> list[str]:
        """Return list of known safe individuals."""
        with self._lock:
            return list(self._known_names)

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    @staticmethod
    def _hash_encoding(encoding: np.ndarray) -> str:
        """Create a rough hash of a face encoding for cooldown tracking."""
        # Quantize to 4 significant figures for fuzzy identity
        return str(tuple(round(float(v), 1) for v in encoding[:8]))
