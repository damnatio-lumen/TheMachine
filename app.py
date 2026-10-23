from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROFILE_DIR = DATA_DIR / "profiles"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
VIDEO_DIR = DATA_DIR / "videos"
LOG_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "machine.sqlite3"
for folder in (DATA_DIR, PROFILE_DIR, SNAPSHOT_DIR, VIDEO_DIR, LOG_DIR):
    folder.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_DIR / "machine.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # Local demo video upload limit

CLASSIFICATIONS: dict[str, dict[str, str]] = {
    "UNCLASSIFIED": {"label": "UNCLASSIFIED", "color": "#b7c0c4", "style": "gray"},
    "ADMIN": {"label": "ADMIN", "color": "#f3e849", "style": "admin"},
    "THREAT": {"label": "THREAT · OPERATOR TAG", "color": "#f04444", "style": "red"},
    "VIOLENCE_INDICATOR": {"label": "VIOLENCE INDICATOR", "color": "#f4f4ed", "style": "white-red"},
    "MACHINE_AWARE": {"label": "MACHINE-AWARE", "color": "#f3e849", "style": "yellow-dashed"},
    "ANALOG_INTERFACE": {"label": "ANALOG INTERFACE", "color": "#e9cf42", "style": "yellow-corners"},
    "ASSET": {"label": "ASSET", "color": "#48a5ff", "style": "blue"},
    "PERSON_OF_INTEREST": {"label": "PERSON OF INTEREST · OPERATOR TAG", "color": "#f3a847", "style": "amber"},
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                alias TEXT NOT NULL,
                classification TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
                admin_tag INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                reference_image TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                track_id TEXT NOT NULL DEFAULT '',
                asset_id TEXT,
                image_path TEXT NOT NULL,
                crop_path TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS video_sources (
                id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS biometric_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'COOPERATIVE',
                created_at TEXT NOT NULL,
                FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS biometric_candidates (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                similarity REAL NOT NULL,
                track_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE
            );
            """
        )
        asset_columns = {row[1] for row in conn.execute("PRAGMA table_info(assets)").fetchall()}
        if "biometric_opt_in" not in asset_columns:
            conn.execute("ALTER TABLE assets ADD COLUMN biometric_opt_in INTEGER NOT NULL DEFAULT 0")
        if "auto_expand_opt_in" not in asset_columns:
            conn.execute("ALTER TABLE assets ADD COLUMN auto_expand_opt_in INTEGER NOT NULL DEFAULT 0")
        conn.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('biometric_enabled','0')")
        conn.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('biometric_threshold','0.40')")


def audit(action: str, details: str = "") -> None:
    try:
        with db_connect() as conn:
            conn.execute("INSERT INTO audit_log(at,action,details) VALUES(?,?,?)", (now_iso(), action, details[:1000]))
    except Exception:
        logging.exception("Audit write failed")


def asset_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["admin_tag"] = bool(item.get("admin_tag", 0))
    item["biometric_opt_in"] = bool(item.get("biometric_opt_in", 0))
    item["auto_expand_opt_in"] = bool(item.get("auto_expand_opt_in", 0))
    item["biometric_sample_count"] = int(item.get("biometric_sample_count", 0) or 0)
    item["classification_info"] = CLASSIFICATIONS.get(item["classification"], CLASSIFICATIONS["UNCLASSIFIED"])
    return item


class CameraManager:
    """Local camera stream with anonymous tracking and optional consent-based local face matching."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cap: cv2.VideoCapture | None = None
        self.thread: threading.Thread | None = None
        self.running = False
        self.frame: np.ndarray | None = None  # raw mirrored frame for face crops
        self.display_frame: np.ndarray | None = None  # CCTV-toned frame with overlay for snapshots
        self.jpeg: bytes | None = None
        self.prefetched_frame: np.ndarray | None = None
        self.backend_name = ""
        self.source_type = "CAMERA"
        self.source_label = "LOCAL CAMERA"
        self.video_eof = False
        self.video_frame_interval = 1.0 / 25.0
        self.frame_count = 0
        self.consecutive_read_failures = 0
        self.faces: list[dict[str, Any]] = []
        self.tracks: dict[str, dict[str, Any]] = {}
        self.track_assignments: dict[str, str] = {}  # operator-assigned current-session tags
        self.counter = 0
        self.last_error = ""
        self.asset_cache: dict[str, dict[str, Any]] = {}
        self.asset_cache_at = 0.0
        self.biometric_template_cache: list[tuple[str, np.ndarray]] = []
        self.biometric_template_cache_at = 0.0
        self.biometric_candidate_proposal_at: dict[str, float] = {}
        self.biometric_links: dict[str, dict[str, Any]] = {}
        self.biometric_enabled = False
        self.biometric_threshold = 0.40
        self.biometric_match_margin = 0.05
        self.yunet_detector = None
        self.sface_recognizer = None
        self.biometric_models_ready = False
        self.biometric_model_error = "Local biometric models are not installed. Run scripts\\download-biometric-models.ps1."
        self.last_frame_at = ""
        cascade_path = BASE_DIR / "models" / "haarcascade_frontalface_default.xml"
        if not cascade_path.exists():
            cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        self.detector = cv2.CascadeClassifier(str(cascade_path))
        if self.detector.empty():
            logging.warning("OpenCV Haar cascade not loaded: %s", cascade_path)

    def load_biometric_models(self) -> None:
        """Load optional local YuNet/SFace models. The app falls back to Haar detection if absent."""
        yunet_path = BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
        sface_path = BASE_DIR / "models" / "face_recognition_sface_2021dec.onnx"
        self.yunet_detector = None
        self.sface_recognizer = None
        try:
            if yunet_path.exists() and hasattr(cv2, "FaceDetectorYN_create"):
                self.yunet_detector = cv2.FaceDetectorYN_create(str(yunet_path), "", (320, 320), 0.72, 0.30, 5000)
            if sface_path.exists() and hasattr(cv2, "FaceRecognizerSF_create"):
                self.sface_recognizer = cv2.FaceRecognizerSF_create(str(sface_path), "")
            self.biometric_models_ready = bool(self.yunet_detector is not None and self.sface_recognizer is not None)
            if self.biometric_models_ready:
                self.biometric_model_error = ""
                logging.info("YuNet/SFace local models loaded")
            else:
                missing = []
                if not yunet_path.exists(): missing.append(yunet_path.name)
                if not sface_path.exists(): missing.append(sface_path.name)
                if missing:
                    self.biometric_model_error = "Missing model files: " + ", ".join(missing) + ". Run scripts\\download-biometric-models.ps1."
                else:
                    self.biometric_model_error = "This OpenCV build does not expose the YuNet/SFace APIs required by the local biometric engine."
                logging.info(self.biometric_model_error)
        except Exception as exc:
            self.biometric_models_ready = False
            self.biometric_model_error = f"Biometric model load failed: {type(exc).__name__}: {exc}"
            logging.exception("Optional biometric models could not be loaded")

    def load_biometric_settings(self) -> None:
        try:
            with db_connect() as conn:
                values = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM app_settings").fetchall()}
            self.biometric_enabled = values.get("biometric_enabled", "0") == "1" and self.biometric_models_ready
            self.biometric_threshold = min(0.70, max(0.30, float(values.get("biometric_threshold", "0.40"))))
        except Exception:
            self.biometric_enabled = False
            self.biometric_threshold = 0.40
            logging.exception("Could not read biometric settings")

    def _detect_face_data(self, raw: np.ndarray, gray: np.ndarray) -> tuple[list[tuple[int, int, int, int]], list[np.ndarray | None]]:
        """Return face boxes and optional normalized SFace embeddings, with Haar fallback."""
        boxes: list[tuple[int, int, int, int]] = []
        feature_by_box: dict[tuple[int, int, int, int], np.ndarray | None] = {}
        h_img, w_img = raw.shape[:2]
        if self.yunet_detector is not None:
            self.yunet_detector.setInputSize((w_img, h_img))
            _retval, faces = self.yunet_detector.detect(raw)
            if faces is not None:
                for face_row in faces:
                    score = float(face_row[14]) if len(face_row) > 14 else 1.0
                    if score < 0.72:
                        continue
                    x, y, w, h = (int(round(float(v))) for v in face_row[:4])
                    box = (x, y, w, h)
                    feature = None
                    if self.biometric_enabled and self.sface_recognizer is not None and (self.frame_count % 3 == 0):
                        try:
                            aligned = self.sface_recognizer.alignCrop(raw, np.asarray(face_row, dtype=np.float32).reshape(1, -1))
                            vector = np.asarray(self.sface_recognizer.feature(aligned), dtype=np.float32).reshape(-1)
                            norm = float(np.linalg.norm(vector))
                            if norm > 1e-8 and np.isfinite(vector).all():
                                feature = vector / norm
                        except Exception:
                            logging.debug("SFace feature extraction failed for one detection", exc_info=True)
                    # Keep the feature linked to the detector's exact box before geometric filtering.
                    if box not in feature_by_box or feature is not None:
                        feature_by_box[box] = feature
                    boxes.append(box)
            filtered = self._filter_detections(boxes, w_img, h_img)
            return filtered, [feature_by_box.get(box) for box in filtered]

        if not self.detector.empty():
            found = self.detector.detectMultiScale(
                gray, scaleFactor=1.10, minNeighbors=7,
                minSize=(56, 56), flags=cv2.CASCADE_SCALE_IMAGE,
            )
            raw_boxes = [(int(x), int(y), int(w), int(h)) for x, y, w, h in found]
            filtered = self._filter_detections(raw_boxes, w_img, h_img)
            return filtered, [None] * len(filtered)
        return [], []

    def _refresh_biometric_template_cache(self, force: bool = False) -> None:
        if not force and time.time() - self.biometric_template_cache_at < 1.0:
            return
        templates: list[tuple[str, np.ndarray]] = []
        try:
            with db_connect() as conn:
                rows = conn.execute(
                    "SELECT t.asset_id,t.embedding_json FROM biometric_templates t JOIN assets a ON a.id=t.asset_id WHERE a.biometric_opt_in=1"
                ).fetchall()
            for row in rows:
                try:
                    vector = np.asarray(json.loads(row["embedding_json"]), dtype=np.float32).reshape(-1)
                    norm = float(np.linalg.norm(vector))
                    if norm > 1e-8 and np.isfinite(vector).all():
                        templates.append((str(row["asset_id"]), vector / norm))
                except (ValueError, TypeError, json.JSONDecodeError):
                    logging.warning("Skipping malformed biometric template for %s", row["asset_id"])
            self.biometric_template_cache = templates
            self.biometric_template_cache_at = time.time()
        except sqlite3.Error:
            logging.exception("Could not refresh local biometric templates")

    def _match_biometric(self, feature: np.ndarray) -> tuple[str | None, float]:
        """Return a clear best enrolled-profile match, or no match when ambiguous.

        Each profile is scored by its best active template. If the top two different
        profiles are too close, the matcher refuses to guess and leaves the face gray.
        """
        self._refresh_biometric_template_cache()
        score_by_asset: dict[str, float] = {}
        for asset_id, template in self.biometric_template_cache:
            if template.shape != feature.shape:
                continue
            score = float(np.dot(template, feature))
            if np.isfinite(score):
                score_by_asset[asset_id] = max(score, score_by_asset.get(asset_id, -1.0))
        ranked = sorted(score_by_asset.items(), key=lambda pair: pair[1], reverse=True)
        if not ranked:
            return None, -1.0
        best_asset, best_score = ranked[0]
        margin = float(getattr(self, "biometric_match_margin", 0.05))
        if len(ranked) > 1 and best_score - ranked[1][1] < margin:
            return None, best_score
        return best_asset, best_score

    def _propose_expansion_sample(self, asset_id: str, track_id: str, feature: np.ndarray, score: float) -> None:
        """Store an opt-in, high-match candidate for review; never auto-activate it."""
        now_mono = time.monotonic()
        key = f"{asset_id}:{track_id}"
        if now_mono - self.biometric_candidate_proposal_at.get(key, 0.0) < 45.0:
            return
        self.biometric_candidate_proposal_at[key] = now_mono
        try:
            with db_connect() as conn:
                row = conn.execute("SELECT biometric_opt_in,auto_expand_opt_in FROM assets WHERE id=?", (asset_id,)).fetchone()
                if not row or not row["biometric_opt_in"] or not row["auto_expand_opt_in"]:
                    return
                conn.execute(
                    "INSERT INTO biometric_candidates(id,asset_id,embedding_json,similarity,track_id,created_at,status) VALUES(?,?,?,?,?,?,?)",
                    ("BIC-" + uuid.uuid4().hex[:10].upper(), asset_id, json.dumps(feature.astype(float).tolist(), separators=(",", ":")), round(float(score), 5), track_id, now_iso(), "PENDING"),
                )
            audit("BIOMETRIC_SAMPLE_PROPOSED", f"asset={asset_id}; track={track_id}; score={score:.4f}")
        except sqlite3.Error:
            logging.exception("Could not create biometric candidate")

    def _update_biometric_matches(self, face_rows: list[dict[str, Any]]) -> None:
        """Re-identify only opt-in enrolled assets and require repeated matches before relinking."""
        if not self.biometric_enabled or not self.biometric_models_ready:
            return
        self._refresh_biometric_template_cache()
        threshold = self.biometric_threshold
        for face in face_rows:
            track_id = face["track_id"]
            track = self.tracks.get(track_id)
            if not track or not face.get("detected", True):
                continue
            feature = face.get("embedding")
            if feature is None or int(track.get("embedding_frame", -1)) != self.frame_count:
                continue
            best_asset_id, best_score = self._match_biometric(feature)
            linked = self.biometric_links.get(track_id)
            if best_asset_id and best_score >= threshold:
                if track.get("bio_candidate_id") == best_asset_id:
                    track["bio_candidate_hits"] = track.get("bio_candidate_hits", 0) + 1
                else:
                    track["bio_candidate_id"] = best_asset_id
                    track["bio_candidate_hits"] = 1
                # Three successive qualifying frames reduce one-frame accidental matches.
                if track.get("bio_candidate_hits", 0) >= 3:
                    if linked and linked.get("asset_id") != best_asset_id:
                        if best_score < float(linked.get("score", 0.0)) + 0.08:
                            continue
                    self.biometric_links[track_id] = {"asset_id": best_asset_id, "score": best_score, "matched_at": now_iso()}
                    track["biometric_misses"] = 0
                    self._refresh_asset_cache()
                    asset = self.asset_cache.get(best_asset_id)
                    if asset and asset.get("auto_expand_opt_in") and best_score >= threshold + 0.10:
                        self._propose_expansion_sample(best_asset_id, track_id, feature, best_score)
            else:
                track["bio_candidate_id"] = None
                track["bio_candidate_hits"] = 0
                if linked:
                    # Small hysteresis protects a stable match from score jitter.
                    if best_asset_id == linked.get("asset_id") and best_score >= threshold - 0.04:
                        linked["score"] = best_score
                        track["biometric_misses"] = 0
                    else:
                        track["biometric_misses"] = track.get("biometric_misses", 0) + 1
                        if track["biometric_misses"] >= 8:
                            self.biometric_links.pop(track_id, None)

    def start(self, index: int = 0) -> tuple[bool, str]:
        """Open the camera and verify that it produces a frame before reporting LIVE.

        Windows devices sometimes open successfully with one OpenCV backend but do
        not deliver frames. Try DirectShow, Media Foundation, then OpenCV's default.
        """
        with self.lock:
            if self.running:
                return True, "Camera already running"

            if os.name == "nt":
                backends = [
                    ("DirectShow", cv2.CAP_DSHOW),
                    ("Media Foundation", cv2.CAP_MSMF),
                    ("Default", cv2.CAP_ANY),
                ]
            else:
                backends = [("Default", cv2.CAP_ANY)]

            cap = None
            first_frame = None
            selected_backend = ""
            failures: list[str] = []
            seen_backends: set[int] = set()

            for backend_name, backend_id in backends:
                if backend_id in seen_backends:
                    continue
                seen_backends.add(backend_id)
                candidate = None
                try:
                    candidate = cv2.VideoCapture(index, backend_id)
                    if not candidate.isOpened():
                        failures.append(f"{backend_name}: device did not open")
                        candidate.release()
                        continue
                    candidate.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    candidate.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                    candidate.set(cv2.CAP_PROP_FPS, 25)
                    try:
                        candidate.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass

                    # Some webcams return empty frames for the first few reads.
                    for _ in range(12):
                        ok, probe = candidate.read()
                        if ok and probe is not None and probe.size:
                            first_frame = probe
                            cap = candidate
                            selected_backend = backend_name
                            break
                        time.sleep(0.12)
                    if cap is not None:
                        break
                    failures.append(f"{backend_name}: opened but returned no frames")
                    candidate.release()
                except Exception as exc:
                    failures.append(f"{backend_name}: {type(exc).__name__}: {exc}")
                    if candidate is not None:
                        candidate.release()

            if cap is None or first_frame is None:
                self.last_error = "Webcam detected/opened, but no video frames were received. " + " | ".join(failures)
                self.running = False
                logging.error("Camera start failed for index %s: %s", index, self.last_error)
                return False, self.last_error

            self.cap = cap
            self.prefetched_frame = first_frame
            self.backend_name = selected_backend
            # A newly opened camera starts a new tracking session. Never carry
            # manual profile tags across a feed restart or source switch.
            self.faces = []
            self.tracks.clear()
            self.track_assignments.clear()
            getattr(self, "biometric_links", {}).clear()
            self.source_type = "CAMERA"
            self.source_label = f"CAMERA {index}"
            self.video_eof = False
            self.video_frame_interval = 1.0 / 25.0
            self.frame_count = 0
            self.consecutive_read_failures = 0
            self.last_frame_at = ""
            self.jpeg = None
            self.running = True
            self.last_error = ""
            self.thread = threading.Thread(target=self._run_loop_guarded, daemon=True, name="machine-camera")
            self.thread.start()

        audit("CAMERA_START", f"index={index}; backend={selected_backend}")
        return True, f"Camera started · {selected_backend} · first frame received"

    def start_video(self, video_path: Path, source_label: str) -> tuple[bool, str]:
        """Start an operator-selected local video file as an alternate frame source."""
        with self.lock:
            if self.running:
                return False, "Stop the current feed before switching source."
            cap = None
            try:
                cap = cv2.VideoCapture(str(video_path))
                if not cap.isOpened():
                    cap.release()
                    return False, "OpenCV could not open this video. Try MP4/H.264, AVI, or another locally supported codec."
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                if not (5.0 <= fps <= 60.0):
                    fps = 25.0
                first_frame = None
                for _ in range(8):
                    ok, probe = cap.read()
                    if ok and probe is not None and probe.size:
                        first_frame = probe
                        break
                    time.sleep(0.05)
                if first_frame is None:
                    cap.release()
                    return False, "The video opened but no readable frame was found. Check the file and codec."
                with self.lock:
                    self.cap = cap
                    self.prefetched_frame = first_frame
                    self.backend_name = "OpenCV VideoCapture"
                    self.source_type = "VIDEO"
                    self.source_label = source_label[:160]
                    self.video_eof = False
                    self.video_frame_interval = 1.0 / fps
                    self.frame_count = 0
                    self.consecutive_read_failures = 0
                    self.last_frame_at = ""
                    self.jpeg = None
                    self.running = True
                    self.last_error = ""
                    self.faces = []
                    self.tracks.clear()
                    self.track_assignments.clear()
                    getattr(self, "biometric_links", {}).clear()
                    self.thread = threading.Thread(target=self._run_loop_guarded, daemon=True, name="machine-video")
                    self.thread.start()
            except Exception as exc:
                if cap is not None:
                    cap.release()
                self.last_error = f"Video start failed: {type(exc).__name__}: {exc}"
                logging.exception("Video source start failed")
                return False, self.last_error
        audit("VIDEO_START", source_label)
        return True, f"Video file opened · {source_label}"

    def _run_loop_guarded(self) -> None:
        try:
            self._loop()
        except Exception as exc:
            logging.exception("Camera processing loop crashed")
            with self.lock:
                self.last_error = f"Camera processing error: {type(exc).__name__}: {exc}"
                self.running = False
                cap = self.cap
                self.cap = None
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    def stop(self) -> None:
        with self.lock:
            self.running = False
            cap = self.cap
            self.cap = None
        if cap is not None:
            cap.release()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        with self.lock:
            self.faces = []
            self.tracks.clear()
            self.track_assignments.clear()
            getattr(self, "biometric_links", {}).clear()
            self.frame = None
            self.display_frame = None
            self.jpeg = None
            self.prefetched_frame = None
            self.backend_name = ""
            self.source_type = "CAMERA"
            self.source_label = "LOCAL CAMERA"
            self.video_eof = False
            self.consecutive_read_failures = 0
        audit("CAMERA_STOP", "")

    def _assign_tracks(self, detections: list[tuple[int, int, int, int]], detection_embeddings: list[np.ndarray | None] | None = None) -> list[dict[str, Any]]:
        """Temporally confirm detections and preserve track IDs across short gaps.

        This layer performs geometric tracking only. Optional biometric comparison is
        handled separately by _update_biometric_matches and is disabled by default.
        New detections must persist for three frames before becoming visible.
        """
        now = time.monotonic()
        previous = self.tracks
        used_tracks: set[str] = set()
        used_detections: set[int] = set()
        matches: list[tuple[float, str, int]] = []

        def iou(box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]) -> float:
            ax, ay, aw, ah = box_a
            bx, by, bw, bh = box_b
            left, top = max(ax, bx), max(ay, by)
            right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
            intersection = max(0.0, right-left) * max(0.0, bottom-top)
            union = aw*ah + bw*bh - intersection
            return intersection / union if union > 0 else 0.0

        # Build candidate pairs and choose best overlaps first. This avoids the old
        # nearest-centre-only matcher swapping IDs when boxes overlap or move quickly.
        for track_id, old in previous.items():
            if now - old.get("last_seen", 0.0) > 0.95:
                continue
            old_box = (old["x"], old["y"], old["w"], old["h"])
            for idx, (x, y, w, h) in enumerate(detections):
                old_area = max(1.0, float(old["w"] * old["h"]))
                new_area = max(1.0, float(w * h))
                size_ratio = new_area / old_area
                if not 0.48 <= size_ratio <= 2.05:
                    continue
                old_cx, old_cy = old["x"] + old["w"] / 2, old["y"] + old["h"] / 2
                cx, cy = x + w / 2, y + h / 2
                dist = ((cx-old_cx)**2 + (cy-old_cy)**2) ** 0.5
                gate = max(58.0, max(w, h, old["w"], old["h"]) * 0.92)
                overlap = iou(old_box, (x, y, w, h))
                if overlap >= 0.08 or dist <= gate:
                    score = overlap * 2.0 - min(1.5, dist / gate) * 0.40 - abs(1.0-size_ratio) * 0.12
                    matches.append((score, track_id, idx))
        matches.sort(reverse=True)
        detection_to_track: dict[int, str] = {}
        for _score, track_id, idx in matches:
            if track_id in used_tracks or idx in used_detections:
                continue
            used_tracks.add(track_id)
            used_detections.add(idx)
            detection_to_track[idx] = track_id

        next_tracks: dict[str, dict[str, Any]] = {}
        rows: list[dict[str, Any]] = []
        smoothing = 0.62

        detection_embeddings = detection_embeddings or [None] * len(detections)
        for idx, (x, y, w, h) in enumerate(detections):
            feature = detection_embeddings[idx] if idx < len(detection_embeddings) else None
            track_id = detection_to_track.get(idx)
            if track_id is None:
                self.counter += 1
                track_id = f"F-{self.counter:04d}"
                track = {
                    "track_id": track_id, "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "cx": x + w/2, "cy": y + h/2, "last_seen": now, "last_frame": self.frame_count,
                    "misses": 0, "hit_streak": 1, "confirmed": False, "hits": 1,
                    "vx": 0.0, "vy": 0.0, "detected": True,
                    "box_history": [(int(x), int(y), int(w), int(h))],
                    "embedding": feature, "embedding_frame": self.frame_count if feature is not None else -1,
                    "bio_candidate_id": None, "bio_candidate_hits": 0, "biometric_misses": 0,
                }
            else:
                old = previous[track_id]
                old_cx, old_cy = old["x"] + old["w"]/2, old["y"] + old["h"]/2
                new_cx, new_cy = x + w/2, y + h/2
                consecutive = old.get("misses", 0) == 0 and old.get("last_frame", -2) >= self.frame_count - 1
                vx = 0.55 * old.get("vx", 0.0) + 0.40 * (new_cx-old_cx)
                vy = 0.55 * old.get("vy", 0.0) + 0.40 * (new_cy-old_cy)

                # Keep a short rolling window and use the median to reject one-frame
                # Haar box expansions/shrinks. Position and size use different gains:
                # movement stays responsive while the rectangle dimensions change slowly.
                history = list(old.get("box_history", []))
                history.append((int(x), int(y), int(w), int(h)))
                history = history[-5:]
                median_box = np.median(np.asarray(history, dtype=np.float32), axis=0)
                median_x, median_y, median_w, median_h = (float(v) for v in median_box)
                median_cx, median_cy = median_x + median_w / 2, median_y + median_h / 2
                position_alpha = 0.48
                size_alpha = 0.24
                old_w, old_h = float(old["w"]), float(old["h"])
                # Cap per-frame size jumps; real scale changes accumulate smoothly.
                target_w = min(old_w * 1.12, max(old_w * 0.88, median_w))
                target_h = min(old_h * 1.12, max(old_h * 0.88, median_h))
                stable_w = max(1.0, old_w * (1.0 - size_alpha) + target_w * size_alpha)
                stable_h = max(1.0, old_h * (1.0 - size_alpha) + target_h * size_alpha)
                stable_cx = old_cx * (1.0 - position_alpha) + median_cx * position_alpha
                stable_cy = old_cy * (1.0 - position_alpha) + median_cy * position_alpha
                stable_x = int(round(stable_cx - stable_w / 2))
                stable_y = int(round(stable_cy - stable_h / 2))
                track = dict(old)
                track.update({
                    "x": stable_x, "y": stable_y,
                    "w": int(round(stable_w)), "h": int(round(stable_h)),
                    "cx": new_cx, "cy": new_cy, "last_seen": now,
                    "last_frame": self.frame_count, "misses": 0,
                    "hit_streak": (old.get("hit_streak", 0)+1) if consecutive else 1,
                    "hits": old.get("hits", 0)+1, "vx": max(-28.0, min(28.0, vx)),
                    "vy": max(-28.0, min(28.0, vy)), "detected": True,
                    "box_history": history,
                    "embedding": feature if feature is not None else old.get("embedding"),
                    "embedding_frame": self.frame_count if feature is not None else old.get("embedding_frame", -1),
                })
                track["confirmed"] = old.get("confirmed", False) or track["hit_streak"] >= 3
            next_tracks[track_id] = track
            if track.get("confirmed", False):
                rows.append(dict(track))

        # Keep already-confirmed tracks alive briefly across a dropped detection.
        # Predict only a small bounded step; remove stale tracks and their manual tag.
        for track_id, old in previous.items():
            if track_id in next_tracks:
                continue
            missed = dict(old)
            missed["misses"] = old.get("misses", 0) + 1
            age = now - old.get("last_seen", 0.0)
            if old.get("confirmed", False) and age <= 0.95 and missed["misses"] <= 24:
                # Maintain an operator-linked track through short detector dropouts.
                # This is positional continuity only, not identity recognition.
                missed["x"] = int(round(old["x"] + max(-7.0, min(7.0, old.get("vx", 0.0)))))
                missed["y"] = int(round(old["y"] + max(-7.0, min(7.0, old.get("vy", 0.0)))))
                missed["last_frame"] = self.frame_count
                missed["detected"] = False
                next_tracks[track_id] = missed
                rows.append(dict(missed))
            elif age <= 0.95 and not old.get("confirmed", False):
                # Unconfirmed candidates disappear as soon as detection drops.
                continue
            else:
                self.track_assignments.pop(track_id, None)
                getattr(self, "biometric_links", {}).pop(track_id, None)

        self.tracks = next_tracks
        return rows

    @staticmethod
    def _filter_detections(
        detections: list[tuple[int, int, int, int]], frame_width: int, frame_height: int
    ) -> list[tuple[int, int, int, int]]:
        """Reject implausible boxes and near-duplicates before temporal tracking."""
        valid: list[tuple[int, int, int, int]] = []
        for x, y, w, h in detections:
            if w < 54 or h < 54 or w > frame_width * 0.52 or h > frame_height * 0.68:
                continue
            aspect = w / float(max(1, h))
            if not 0.68 <= aspect <= 1.42:
                continue
            if x < 0 or y < 0 or x + w > frame_width or y + h > frame_height:
                continue
            valid.append((int(x), int(y), int(w), int(h)))
        # Haar may return overlapping rectangles around the same face. Keep the
        # larger/stronger-looking rectangle and suppress overlaps over 45% IoU.
        valid.sort(key=lambda box: box[2] * box[3], reverse=True)
        kept: list[tuple[int, int, int, int]] = []
        for box in valid:
            x, y, w, h = box
            duplicate = False
            for ox, oy, ow, oh in kept:
                left, top = max(x, ox), max(y, oy)
                right, bottom = min(x+w, ox+ow), min(y+h, oy+oh)
                inter = max(0, right-left) * max(0, bottom-top)
                union = w*h + ow*oh - inter
                if union > 0 and inter / union >= 0.40:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(box)
        return kept

    def _refresh_asset_cache(self, force: bool = False) -> None:
        if not force and time.time() - self.asset_cache_at < 0.75:
            return
        try:
            with db_connect() as conn:
                rows = conn.execute("SELECT * FROM assets").fetchall()
            self.asset_cache = {row["id"]: asset_to_dict(row) for row in rows}
            self.asset_cache_at = time.time()
        except sqlite3.Error:
            logging.exception("Unable to refresh asset cache")

    def _get_assignment(self, track_id: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
        asset_id = self.track_assignments.get(track_id)
        if not asset_id and self.biometric_enabled:
            match = self.biometric_links.get(track_id)
            asset_id = match.get("asset_id") if match else None
        if not asset_id:
            return None, CLASSIFICATIONS["UNCLASSIFIED"]
        self._refresh_asset_cache()
        asset = self.asset_cache.get(asset_id)
        if not asset:
            self.track_assignments.pop(track_id, None)
            return None, CLASSIFICATIONS["UNCLASSIFIED"]
        cls = dict(asset["classification_info"])
        if asset["admin_tag"]:
            cls["label"] = "ADMIN"
            cls["color"] = "#f3e849"
            cls["style"] = "admin"
        return asset, cls

    @staticmethod
    def _draw_dashed_rect(frame: np.ndarray, x: int, y: int, w: int, h: int, color: tuple[int, int, int], dash: int = 12, thick: int = 2) -> None:
        """Draw an OpenCV dashed rectangle using integer pixel coordinates only.

        OpenCV's Python bindings reject float coordinates. Keep the dash step
        integer as well; using ``dash * 1.65`` directly turns ``cur`` into a
        float after the first segment and crashes the live camera processing loop.
        """
        x, y, w, h = (int(round(v)) for v in (x, y, w, h))
        dash = max(1, int(round(dash)))
        thick = max(1, int(round(thick)))
        step = max(1, int(round(dash * 1.65)))
        x2, y2 = int(x + w), int(y + h)
        for x1, xa in ((x, x2),):
            for yy in (y, y2):
                cur = int(x1)
                while cur < xa:
                    end = int(min(cur + dash, xa))
                    cv2.line(frame, (int(cur), int(yy)), (end, int(yy)), color, thick, cv2.LINE_AA)
                    cur = int(cur + step)
        for xx in (x, x2):
            cur = int(y)
            while cur < y2:
                end = int(min(cur + dash, y2))
                cv2.line(frame, (int(xx), int(cur)), (int(xx), end), color, thick, cv2.LINE_AA)
                cur = int(cur + step)
        # POI-inspired registration ticks
        tick = int(max(9, min(w, h) // 9))
        for px, py, sx, sy in ((x, y, 1, 1), (x2, y, -1, 1), (x, y2, 1, -1), (x2, y2, -1, -1)):
            cv2.line(frame, (int(px), int(py)), (int(px + sx * tick), int(py)), color, thick + 1, cv2.LINE_AA)
            cv2.line(frame, (int(px), int(py)), (int(px), int(py + sy * tick)), color, thick + 1, cv2.LINE_AA)

    def _loop(self) -> None:
        while True:
            iteration_started = time.monotonic()
            with self.lock:
                if not self.running or self.cap is None:
                    break
                cap = self.cap
                raw = self.prefetched_frame
                if raw is not None:
                    self.prefetched_frame = None
            if raw is None:
                ok, raw = cap.read()
            else:
                ok = True
            if not ok or raw is None:
                with self.lock:
                    self.consecutive_read_failures += 1
                    is_video = self.source_type == "VIDEO"
                    if is_video and self.consecutive_read_failures >= 2:
                        self.last_error = "Video playback ended or the decoder could not read the next frame."
                        self.running = False
                        cap_to_release = self.cap
                        self.cap = None
                        self.video_eof = True
                        logging.info("Video source finished: %s", self.source_label)
                    else:
                        cap_to_release = None
                        self.last_error = "Camera opened but stopped delivering frames. Try another device index, close other camera apps, or check Windows camera permissions."
                        if self.consecutive_read_failures in (1, 10, 30):
                            logging.warning("Camera read failed (%s consecutive failures)", self.consecutive_read_failures)
                if cap_to_release is not None:
                    try: cap_to_release.release()
                    except Exception: pass
                if is_video and self.consecutive_read_failures >= 2:
                    break
                time.sleep(0.08)
                continue
            # Mirror only an actual webcam preview. Video files retain their original orientation.
            with self.lock:
                mirror_frame = self.source_type == "CAMERA"
            if mirror_frame:
                raw = cv2.flip(raw, 1)
            gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
            # Haar fallback uses a slight blur. YuNet, when installed, supplies landmarks for SFace.
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
            detections, detection_embeddings = self._detect_face_data(raw, gray)
            with self.lock:
                face_rows = self._assign_tracks(detections, detection_embeddings)
                self._update_biometric_matches(face_rows)
                # CCTV-like monochrome with a restrained green/teal cast; raw stays unchanged for face crops.
                mono = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY).astype(np.float32)
                display = np.stack((mono * 0.76, mono * 0.98, mono * 0.84), axis=-1).clip(0, 255).astype(np.uint8)
                display = cv2.convertScaleAbs(display, alpha=1.10, beta=-7)
                h_img, w_img = display.shape[:2]
                for face in face_rows:
                    x,y,w,h = face["x"],face["y"],face["w"],face["h"]
                    asset, cls = self._get_assignment(face["track_id"])
                    hex_color = cls.get("color", "#b7c0c4").lstrip("#")
                    bgr = tuple(int(hex_color[i:i+2],16) for i in (4,2,0))
                    style = cls.get("style", "gray")
                    if style in ("gray", "yellow-dashed", "white-red", "admin"):
                        # POI-inspired brackets: dashed perimeter plus heavier corner ticks.
                        self._draw_dashed_rect(display, x, y, w, h, bgr, dash=9 if style == "admin" else 11, thick=2)
                        if style == "white-red":
                            red = (68, 68, 240)
                            tick = max(7, min(w, h) // 11)
                            for px, py, sx, sy in ((x,y,1,1),(x+w,y,-1,1),(x,y+h,1,-1),(x+w,y+h,-1,-1)):
                                cv2.line(display,(px,py),(px+sx*tick,py),red,3,cv2.LINE_AA)
                                cv2.line(display,(px,py),(px,py+sy*tick),red,3,cv2.LINE_AA)
                    else:
                        cv2.rectangle(display,(x,y),(x+w,y+h),bgr,2,cv2.LINE_AA)
                        tick = max(8, min(w,h)//7)
                        for px,py,sx,sy in ((x,y,1,1),(x+w,y,-1,1),(x,y+h,1,-1),(x+w,y+h,-1,-1)):
                            cv2.line(display,(px,py),(px+sx*tick,py),bgr,4,cv2.LINE_AA)
                            cv2.line(display,(px,py),(px,py+sy*tick),bgr,4,cv2.LINE_AA)
                    label = ("ADMIN" if asset and asset.get("admin_tag") else cls.get("label", "UNCLASSIFIED")) if asset else ""
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    scale = max(0.40, min(0.58, w / 280.0))
                    # Registered roles use a compact dossier-like tag. ADMIN gets
                    # the show's yellow reticle and a narrow yellow-on-black label.
                    if label and label != "UNCLASSIFIED":
                        label = label[:34]
                        (tw, th), baseline = cv2.getTextSize(label, font, scale, 1)
                        pad_x, pad_y = 6, 4
                        tag_w, tag_h = tw + 2 * pad_x, th + baseline + 2 * pad_y
                        if style == "admin":
                            tag_x = max(8, min(w_img - tag_w - 8, x))
                            tag_y = y - tag_h - 3
                            if tag_y < 42:
                                tag_y = min(h_img - tag_h - 8, y + 4)
                            cv2.rectangle(display, (tag_x, tag_y), (tag_x + tag_w, tag_y + tag_h), (8, 10, 9), -1)
                            cv2.rectangle(display, (tag_x, tag_y), (tag_x + tag_w, tag_y + tag_h), bgr, 1, cv2.LINE_AA)
                            cv2.putText(display, label, (tag_x + pad_x, tag_y + pad_y + th), font, scale, bgr, 1, cv2.LINE_AA)
                        else:
                            ly = max(42 + tag_h, y - 3)
                            cv2.rectangle(display, (x, ly - tag_h), (x + tag_w, ly), bgr, -1)
                            cv2.putText(display, label, (x + pad_x, ly - pad_y - baseline), font, scale, (10,12,12), 1, cv2.LINE_AA)
                    # Track ID is intentionally subdued; classification remains the main cue.
                    cv2.putText(display, face["track_id"], (x, min(h_img-8,y+h+18)),font,0.38,(min(255,bgr[0]+20),min(255,bgr[1]+20),min(255,bgr[2]+20)),1,cv2.LINE_AA)
                # CCTV frame information
                cv2.rectangle(display,(0,0),(w_img,38),(7,10,10),-1)
                cv2.putText(display,"THE MACHINE  //  CAMERA MODE",(16,25),cv2.FONT_HERSHEY_SIMPLEX,0.62,(243,232,73),1,cv2.LINE_AA)
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cv2.putText(display,stamp,(max(16,w_img-235),25),cv2.FONT_HERSHEY_SIMPLEX,0.48,(180,194,194),1,cv2.LINE_AA)
                cv2.rectangle(display,(8,46),(w_img-9,h_img-9),(43,84,76),1)
                ok_jpg, buffer = cv2.imencode(".jpg", display, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if ok_jpg:
                    self.jpeg = buffer.tobytes()
                self.display_frame = display.copy()
                self.frame = raw.copy()
                self.faces = face_rows
                self.last_frame_at = now_iso()
                self.frame_count += 1
                self.consecutive_read_failures = 0
                self.last_error = ""
            with self.lock:
                source_is_video = self.source_type == "VIDEO"
                frame_interval = self.video_frame_interval
            elapsed = time.monotonic() - iteration_started
            time.sleep(max(0.001, frame_interval - elapsed) if source_is_video else 0.012)
        with self.lock:
            self.running = False
            if self.video_eof:
                self.faces = []
                self.tracks.clear()
                self.track_assignments.clear()

    def status(self) -> dict[str, Any]:
        with self.lock:
            faces = []
            for face in self.faces:
                asset, cls = self._get_assignment(face["track_id"])
                faces.append({
                    "track_id": face["track_id"], "x": face["x"], "y": face["y"], "w": face["w"], "h": face["h"],
                    "asset_id": asset["id"] if asset else None,
                    "alias": asset["alias"] if asset else "",
                    "classification": asset["classification"] if asset else "UNCLASSIFIED",
                    "label": "ADMIN" if asset and asset["admin_tag"] else cls["label"],
                    "color": cls["color"], "style": cls["style"],
                    "admin_tag": asset["admin_tag"] if asset else False,
                    "detected_now": bool(face.get("detected", True)),
                    "stable_frames": int(face.get("hits", 1)),
                    "matched_by": "MANUAL" if face["track_id"] in self.track_assignments else ("BIOMETRIC" if face["track_id"] in self.biometric_links and self.biometric_enabled else "NONE"),
                    "match_score": round(float(self.biometric_links.get(face["track_id"], {}).get("score", 0.0)), 4) if face["track_id"] in self.biometric_links else None,
                })
            return {
                "running": self.running,
                "frame_ready": self.jpeg is not None,
                "last_error": self.last_error,
                "last_frame_at": self.last_frame_at,
                "frame_count": self.frame_count,
                "consecutive_read_failures": self.consecutive_read_failures,
                "backend": self.backend_name,
                "source_type": self.source_type,
                "source_label": self.source_label,
                "video_eof": self.video_eof,
                "biometric_models_ready": self.biometric_models_ready,
                "biometric_enabled": self.biometric_enabled,
                "biometric_threshold": self.biometric_threshold,
                "biometric_model_error": self.biometric_model_error,
                "biometric_template_count": len(self.biometric_template_cache),
                "faces": faces,
            }

    def current_face_crop(self, track_id: str) -> np.ndarray:
        with self.lock:
            if self.frame is None:
                raise ValueError("No camera frame available")
            face = next((f for f in self.faces if f["track_id"] == track_id), None)
            if face is None:
                raise ValueError("Selected face is no longer visible. Select it again.")
            frame = self.frame.copy()
            x,y,w,h = face["x"],face["y"],face["w"],face["h"]
        H,W = frame.shape[:2]
        pad_x, pad_y = int(w*0.35), int(h*0.40)
        x1,y1 = max(0,x-pad_x),max(0,y-pad_y)
        x2,y2 = min(W,x+w+pad_x),min(H,y+h+pad_y)
        crop = frame[y1:y2,x1:x2]
        if crop.size == 0:
            raise ValueError("Could not crop selected face")
        return crop

    def current_frame(self) -> np.ndarray:
        with self.lock:
            if self.display_frame is None:
                raise ValueError("Camera is not running")
            return self.display_frame.copy()


init_db()
camera = CameraManager()
camera.load_biometric_models()
camera.load_biometric_settings()


@app.after_request
def security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    return response


@app.get("/")
def index():
    return render_template("index.html", classifications=CLASSIFICATIONS)


@app.get("/protocol")
def protocol():
    return send_file(BASE_DIR / "docs" / "registration_protocol.md", mimetype="text/plain; charset=utf-8", as_attachment=False, download_name="registration_protocol.txt")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "app": "MACHINE CAMERA MODE", "version": "1.6.0", "time": now_iso(), "camera": camera.status()["running"]})


@app.post("/api/camera/start")
def camera_start():
    payload = request.get_json(silent=True) or {}
    try:
        index = max(0, min(8, int(payload.get("index", 0))))
    except (ValueError, TypeError):
        index = 0
    ok, message = camera.start(index)
    return jsonify({"ok": ok, "message": message, "status": camera.status()}), (200 if ok else 409)


@app.post("/api/camera/stop")
def camera_stop():
    camera.stop()
    return jsonify({"ok": True, "message": "Feed stopped"})


ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".mpg", ".mpeg"}


@app.get("/api/videos")
def list_videos():
    with db_connect() as conn:
        rows = conn.execute("SELECT id,original_name,size_bytes,created_at FROM video_sources ORDER BY created_at DESC").fetchall()
    return jsonify({"videos": [dict(row) for row in rows]})


@app.post("/api/video/upload")
def upload_video():
    uploaded = request.files.get("video")
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "Select a video file first."}), 400
    original_name = secure_filename(Path(uploaded.filename).name) or "local-video"
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        return jsonify({"ok": False, "error": "Unsupported video extension. Use MP4, M4V, MOV, AVI, MKV, WEBM, MPG or MPEG."}), 400
    video_id = "VID-" + uuid.uuid4().hex[:10].upper()
    stored_name = video_id + ext
    target = VIDEO_DIR / stored_name
    try:
        uploaded.save(target)
        size_bytes = target.stat().st_size
        if size_bytes <= 0:
            target.unlink(missing_ok=True)
            return jsonify({"ok": False, "error": "The uploaded file is empty."}), 400
        probe = cv2.VideoCapture(str(target))
        ok, frame = probe.read()
        probe.release()
        if not ok or frame is None or not frame.size:
            target.unlink(missing_ok=True)
            return jsonify({"ok": False, "error": "OpenCV cannot decode the first frame. Try MP4/H.264 or another supported codec."}), 400
        with db_connect() as conn:
            conn.execute("INSERT INTO video_sources(id,original_name,stored_name,size_bytes,created_at) VALUES(?,?,?,?,?)",
                         (video_id, original_name, stored_name, size_bytes, now_iso()))
        audit("VIDEO_UPLOADED", f"{video_id}; {original_name}; {size_bytes} bytes")
        return jsonify({"ok": True, "video": {"id": video_id, "original_name": original_name, "size_bytes": size_bytes, "created_at": now_iso()}, "message": f"Video source added: {original_name}"}), 201
    except Exception as exc:
        target.unlink(missing_ok=True)
        logging.exception("Video upload failed")
        return jsonify({"ok": False, "error": f"Video upload failed: {type(exc).__name__}: {exc}"}), 500


@app.post("/api/video/start")
def video_start():
    payload = request.get_json(silent=True) or {}
    source_id = str(payload.get("source_id", "")).strip()
    if not source_id:
        return jsonify({"ok": False, "error": "Select an uploaded video source."}), 400
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM video_sources WHERE id=?", (source_id,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "Video source not found. Upload it again."}), 404
    video_path = VIDEO_DIR / Path(row["stored_name"]).name
    if not video_path.is_file():
        return jsonify({"ok": False, "error": "Stored video file is missing."}), 404
    ok, message = camera.start_video(video_path, row["original_name"])
    return jsonify({"ok": ok, "message": message, "status": camera.status()}), (200 if ok else 409)


@app.delete("/api/videos/<video_id>")
def delete_video(video_id: str):
    if camera.running and camera.source_type == "VIDEO":
        return jsonify({"ok": False, "error": "Stop the active feed before removing video files."}), 409
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM video_sources WHERE id=?", (video_id,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Video source not found."}), 404
        conn.execute("DELETE FROM video_sources WHERE id=?", (video_id,))
    (VIDEO_DIR / Path(row["stored_name"]).name).unlink(missing_ok=True)
    audit("VIDEO_DELETED", video_id)
    return jsonify({"ok": True})


@app.get("/video_feed")
def video_feed():
    def generate():
        waiting_since = time.monotonic()
        last_sent = None
        while True:
            if not camera.running:
                break
            with camera.lock:
                frame = camera.jpeg
            if frame:
                # MJPEG multipart framing: each JPEG is one independent part.
                yield b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n" + frame + b"\r\n"
                last_sent = time.monotonic()
            elif time.monotonic() - waiting_since > 6:
                logging.warning("MJPEG stream timed out waiting for the first camera frame")
                break
            time.sleep(0.04)
    return Response(
        generate(),
        content_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "X-Accel-Buffering": "no"},
        direct_passthrough=True,
    )


@app.get("/api/status")
def get_status():
    return jsonify(camera.status())


def read_asset_payload(payload: dict[str, Any], *, existing: dict[str, Any] | None = None) -> tuple[str, str, int, str]:
    alias = str(payload.get("alias", existing["alias"] if existing else "")).strip()[:100]
    classification = str(payload.get("classification", existing["classification"] if existing else "UNCLASSIFIED")).strip().upper()
    admin_value = payload.get("admin_tag", existing["admin_tag"] if existing else False)
    admin_tag = admin_value is True or str(admin_value).strip().lower() in ("1", "true", "yes", "on")
    if admin_tag:
        classification = "ADMIN"
    notes = str(payload.get("notes", existing["notes"] if existing else "")).strip()[:3000]
    if not alias:
        raise ValueError("Alias/name is required")
    if classification not in CLASSIFICATIONS:
        raise ValueError("Unknown classification")
    if admin_tag and classification == "THREAT":
        # Avoid presenting an account role as a threat classification. Both fields remain separate in the profile UI.
        pass
    return alias, classification, int(admin_tag), notes


@app.get("/api/assets")
def list_assets():
    with db_connect() as conn:
        rows = conn.execute("SELECT a.*, (SELECT COUNT(*) FROM biometric_templates t WHERE t.asset_id=a.id) AS biometric_sample_count FROM assets a ORDER BY a.updated_at DESC").fetchall()
    return jsonify({"assets": [asset_to_dict(row) for row in rows], "classifications": CLASSIFICATIONS})


@app.post("/api/assets")
def create_asset():
    payload = request.get_json(silent=True) or {}
    if payload.get("consent_ack") is not True:
        return jsonify({"ok": False, "error": "Confirm authorization to store this reference image."}), 400
    biometric_opt_in = payload.get("biometric_opt_in") is True
    auto_expand_opt_in = payload.get("auto_expand_opt_in") is True
    if biometric_opt_in and payload.get("biometric_consent_ack") is not True:
        return jsonify({"ok": False, "error": "Explicit participant opt-in is required before enabling biometric matching."}), 400
    if auto_expand_opt_in and not biometric_opt_in:
        return jsonify({"ok": False, "error": "Template expansion requires biometric matching opt-in."}), 400
    track_id = str(payload.get("track_id", "")).strip()
    if not track_id:
        return jsonify({"ok": False, "error": "Select a currently detected face first."}), 400
    try:
        alias, classification, admin_tag, notes = read_asset_payload(payload)
        crop = camera.current_face_crop(track_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    asset_id = "AST-" + uuid.uuid4().hex[:8].upper()
    image_name = f"{asset_id}.jpg"
    target = PROFILE_DIR / image_name
    if not cv2.imwrite(str(target), crop):
        return jsonify({"ok": False, "error": "Could not save the reference crop."}), 500
    stamp = now_iso()
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO assets(id,alias,classification,admin_tag,notes,reference_image,created_at,updated_at,biometric_opt_in,auto_expand_opt_in) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (asset_id, alias, classification, admin_tag, notes, image_name, stamp, stamp, int(biometric_opt_in), int(auto_expand_opt_in)),
        )
    # Registration links only the current track. Biometric matching requires a separate cooperative enrollment.
    with camera.lock:
        camera.track_assignments[track_id] = asset_id
    camera._refresh_asset_cache(force=True)
    audit("ASSET_CREATED", f"{asset_id}; class={classification}; admin={admin_tag}")
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    return jsonify({"ok": True, "asset": asset_to_dict(row), "message": "Asset registered and manually linked to the selected live track."}), 201


@app.put("/api/assets/<asset_id>")
def update_asset(asset_id: str):
    payload = request.get_json(silent=True) or {}
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Asset not found"}), 404
        try:
            alias, classification, admin_tag, notes = read_asset_payload(payload, existing=dict(row))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        old_bio = bool(row["biometric_opt_in"]) if "biometric_opt_in" in row.keys() else False
        old_expand = bool(row["auto_expand_opt_in"]) if "auto_expand_opt_in" in row.keys() else False
        biometric_opt_in = (payload.get("biometric_opt_in") is True) if "biometric_opt_in" in payload else old_bio
        auto_expand_opt_in = (payload.get("auto_expand_opt_in") is True) if "auto_expand_opt_in" in payload else old_expand
        if biometric_opt_in and not old_bio and payload.get("biometric_consent_ack") is not True:
            return jsonify({"ok": False, "error": "Explicit participant opt-in is required before enabling biometric matching."}), 400
        if auto_expand_opt_in and not biometric_opt_in:
            return jsonify({"ok": False, "error": "Template expansion requires biometric matching opt-in."}), 400
        conn.execute("UPDATE assets SET alias=?,classification=?,admin_tag=?,notes=?,biometric_opt_in=?,auto_expand_opt_in=?,updated_at=? WHERE id=?", (alias, classification, admin_tag, notes, int(biometric_opt_in), int(auto_expand_opt_in), now_iso(), asset_id))
        if not biometric_opt_in:
            conn.execute("DELETE FROM biometric_templates WHERE asset_id=?", (asset_id,))
            conn.execute("DELETE FROM biometric_candidates WHERE asset_id=?", (asset_id,))
        elif not auto_expand_opt_in:
            conn.execute("DELETE FROM biometric_candidates WHERE asset_id=? AND status='PENDING'", (asset_id,))
        updated = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    camera._refresh_asset_cache(force=True)
    camera._refresh_biometric_template_cache(force=True)
    if not bool(updated["biometric_opt_in"]):
        with camera.lock:
            camera.biometric_links = {track: match for track, match in camera.biometric_links.items() if match.get("asset_id") != asset_id}
    audit("ASSET_UPDATED", f"{asset_id}; class={classification}; admin={admin_tag}; biometric={bool(updated['biometric_opt_in'])}")
    return jsonify({"ok": True, "asset": asset_to_dict(updated)})


@app.delete("/api/assets/<asset_id>")
def delete_asset(asset_id: str):
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Asset not found"}), 404
        image_name = row["reference_image"]
        conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
    with camera.lock:
        camera.track_assignments = {key: value for key, value in camera.track_assignments.items() if value != asset_id}
    camera._refresh_asset_cache(force=True)
    camera._refresh_biometric_template_cache(force=True)
    if image_name:
        try:
            (PROFILE_DIR / Path(image_name).name).unlink(missing_ok=True)
        except OSError:
            logging.exception("Unable to remove profile image")
    audit("ASSET_DELETED", asset_id)
    return jsonify({"ok": True})


@app.post("/api/bind")
def bind_asset():
    payload = request.get_json(silent=True) or {}
    track_id = str(payload.get("track_id", "")).strip()
    asset_id = str(payload.get("asset_id", "")).strip()
    if not track_id or not asset_id:
        return jsonify({"ok": False, "error": "track_id and asset_id are required"}), 400
    with camera.lock:
        if not any(f["track_id"] == track_id for f in camera.faces):
            return jsonify({"ok": False, "error": "That face is no longer visible. Select it again."}), 409
        camera.track_assignments[track_id] = asset_id
    with db_connect() as conn:
        row = conn.execute("SELECT id FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not row:
        with camera.lock:
            camera.track_assignments.pop(track_id, None)
        return jsonify({"ok": False, "error": "Asset not found"}), 404
    audit("ASSET_MANUALLY_LINKED", f"{track_id} -> {asset_id}")
    return jsonify({"ok": True, "message": "Profile manually linked for this current track only."})


@app.post("/api/unbind")
def unbind_asset():
    payload = request.get_json(silent=True) or {}
    track_id = str(payload.get("track_id", "")).strip()
    with camera.lock:
        camera.track_assignments.pop(track_id, None)
        camera.biometric_links.pop(track_id, None)
        camera.biometric_candidate_proposal_at = {k: v for k, v in camera.biometric_candidate_proposal_at.items() if not k.endswith(":" + track_id)}
    audit("ASSET_UNLINKED", track_id)
    return jsonify({"ok": True})


@app.get("/api/biometrics/status")
def biometric_status():
    with db_connect() as conn:
        sample_count = int(conn.execute("SELECT COUNT(*) FROM biometric_templates t JOIN assets a ON a.id=t.asset_id WHERE a.biometric_opt_in=1").fetchone()[0])
        enrolled_count = int(conn.execute("SELECT COUNT(DISTINCT t.asset_id) FROM biometric_templates t JOIN assets a ON a.id=t.asset_id WHERE a.biometric_opt_in=1").fetchone()[0])
        pending_count = int(conn.execute("SELECT COUNT(*) FROM biometric_candidates WHERE status='PENDING'").fetchone()[0])
    return jsonify({
        "models_ready": camera.biometric_models_ready,
        "enabled": camera.biometric_enabled,
        "threshold": camera.biometric_threshold,
        "sample_count": sample_count,
        "enrolled_assets": enrolled_count,
        "pending_candidates": pending_count,
        "model_error": camera.biometric_model_error,
    })


@app.post("/api/biometrics/settings")
def update_biometric_settings():
    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled") is True
    try:
        threshold = float(payload.get("threshold", camera.biometric_threshold))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Threshold must be a number between 0.30 and 0.70."}), 400
    if not 0.30 <= threshold <= 0.70:
        return jsonify({"ok": False, "error": "Threshold must be between 0.30 and 0.70."}), 400
    if enabled and not camera.biometric_models_ready:
        return jsonify({"ok": False, "error": camera.biometric_model_error or "Local biometric models are not ready."}), 409
    with db_connect() as conn:
        conn.execute("INSERT INTO app_settings(key,value) VALUES('biometric_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if enabled else "0",))
        conn.execute("INSERT INTO app_settings(key,value) VALUES('biometric_threshold',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(round(threshold, 3)),))
    with camera.lock:
        camera.biometric_enabled = enabled
        camera.biometric_threshold = threshold
        if not enabled:
            camera.biometric_links.clear()
        # New thresholds need fresh, consecutive match evidence before a relock.
        for track in camera.tracks.values():
            track["bio_candidate_id"] = None
            track["bio_candidate_hits"] = 0
    audit("BIOMETRIC_SETTINGS_UPDATED", f"enabled={enabled}; threshold={threshold:.3f}")
    return jsonify({"ok": True, "enabled": enabled, "threshold": threshold, "message": "Biometric matching settings saved locally."})


@app.post("/api/biometrics/enroll")
def enroll_biometric_samples():
    payload = request.get_json(silent=True) or {}
    asset_id = str(payload.get("asset_id", "")).strip()
    track_id = str(payload.get("track_id", "")).strip()
    if not asset_id or not track_id:
        return jsonify({"ok": False, "error": "Select a linked live track and a saved asset first."}), 400
    if not camera.biometric_models_ready:
        return jsonify({"ok": False, "error": camera.biometric_model_error or "Local biometric models are not ready."}), 409
    with db_connect() as conn:
        asset = conn.execute("SELECT a.id,a.alias,a.biometric_opt_in,(SELECT COUNT(*) FROM biometric_templates t WHERE t.asset_id=a.id) AS sample_count FROM assets a WHERE a.id=?", (asset_id,)).fetchone()
    if not asset:
        return jsonify({"ok": False, "error": "Asset not found."}), 404
    replace_existing = payload.get("replace_existing") is True
    if int(asset["sample_count"] or 0) > 0 and not replace_existing:
        return jsonify({"ok": False, "error": "This profile is already enrolled. Confirm RE-ENROLL to replace its active biometric templates with a new cooperative sample set."}), 409
    if not bool(asset["biometric_opt_in"]):
        return jsonify({"ok": False, "error": "Enable biometric matching for this profile only after the participant has explicitly opted in."}), 403
    with camera.lock:
        if not camera.running:
            return jsonify({"ok": False, "error": "Start the live camera or video feed first."}), 409
        if camera.track_assignments.get(track_id) != asset_id:
            return jsonify({"ok": False, "error": "Manually link this selected track to the profile before cooperative enrollment."}), 409
    if not camera.biometric_enabled:
        return jsonify({"ok": False, "error": "Enable local biometric matching in the left panel before enrollment."}), 409
    samples: list[np.ndarray] = []
    last_frame = -1
    next_sample_at = time.monotonic()
    deadline = time.monotonic() + 5.5
    while len(samples) < 5 and time.monotonic() < deadline:
        with camera.lock:
            track = camera.tracks.get(track_id)
            feature = track.get("embedding") if track and track.get("detected", False) else None
            frame_id = int(track.get("embedding_frame", -1)) if track else -1
        now_mono = time.monotonic()
        if feature is not None and frame_id >= 0 and frame_id != last_frame and now_mono >= next_sample_at:
            vector = np.asarray(feature, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            if norm > 1e-8 and np.isfinite(vector).all():
                samples.append(vector / norm)
                last_frame = frame_id
                next_sample_at = now_mono + 0.55
        time.sleep(0.04)
    if len(samples) < 3:
        return jsonify({"ok": False, "error": f"Only {len(samples)} usable samples captured. Keep the face clearly visible and try again."}), 409
    stamp = now_iso()
    with db_connect() as conn:
        # Replace only after enough fresh samples were successfully collected, so a failed
        # scan never destroys the currently working enrollment.
        if replace_existing:
            conn.execute("DELETE FROM biometric_templates WHERE asset_id=?", (asset_id,))
            conn.execute("DELETE FROM biometric_candidates WHERE asset_id=?", (asset_id,))
        for vector in samples:
            conn.execute("INSERT INTO biometric_templates(asset_id,embedding_json,source,created_at) VALUES(?,?,?,?)", (asset_id, json.dumps(vector.astype(float).tolist(), separators=(",", ":")), "COOPERATIVE", stamp))
    camera._refresh_biometric_template_cache(force=True)
    audit("BIOMETRIC_COOPERATIVE_ENROLLMENT", f"asset={asset_id}; samples={len(samples)}; replace={replace_existing}; track={track_id}")
    return jsonify({"ok": True, "asset_id": asset_id, "samples_added": len(samples), "message": f"Cooperative enrollment complete: {len(samples)} local samples saved. Previous templates were replaced only after the new scan succeeded. Future expansion samples remain pending until reviewed."})


@app.get("/api/biometrics/candidates")
def list_biometric_candidates():
    with db_connect() as conn:
        rows = conn.execute("SELECT c.id,c.asset_id,a.alias,c.similarity,c.track_id,c.created_at,c.status FROM biometric_candidates c JOIN assets a ON a.id=c.asset_id WHERE c.status='PENDING' ORDER BY c.created_at DESC LIMIT 100").fetchall()
    return jsonify({"candidates": [dict(row) for row in rows]})


@app.post("/api/biometrics/candidates/<candidate_id>/approve")
def approve_biometric_candidate(candidate_id: str):
    with db_connect() as conn:
        candidate = conn.execute("SELECT c.*,a.biometric_opt_in FROM biometric_candidates c JOIN assets a ON a.id=c.asset_id WHERE c.id=? AND c.status='PENDING'", (candidate_id,)).fetchone()
        if not candidate:
            return jsonify({"ok": False, "error": "Pending candidate not found."}), 404
        if not candidate["biometric_opt_in"]:
            return jsonify({"ok": False, "error": "Biometric consent is no longer enabled for this asset."}), 403
        conn.execute("INSERT INTO biometric_templates(asset_id,embedding_json,source,created_at) VALUES(?,?,?,?)", (candidate["asset_id"], candidate["embedding_json"], "REVIEW_APPROVED", now_iso()))
        conn.execute("UPDATE biometric_candidates SET status='APPROVED' WHERE id=?", (candidate_id,))
    camera._refresh_biometric_template_cache(force=True)
    audit("BIOMETRIC_CANDIDATE_APPROVED", candidate_id)
    return jsonify({"ok": True, "message": "Candidate approved and added to the active template set."})


@app.post("/api/biometrics/candidates/<candidate_id>/reject")
def reject_biometric_candidate(candidate_id: str):
    with db_connect() as conn:
        cur = conn.execute("UPDATE biometric_candidates SET status='REJECTED' WHERE id=? AND status='PENDING'", (candidate_id,))
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "Pending candidate not found."}), 404
    audit("BIOMETRIC_CANDIDATE_REJECTED", candidate_id)
    return jsonify({"ok": True, "message": "Candidate rejected and excluded from matching."})


@app.post("/api/snapshot")
def make_snapshot():
    payload = request.get_json(silent=True) or {}
    track_id = str(payload.get("track_id", "")).strip()
    notes = str(payload.get("notes", "")).strip()[:1000]
    try:
        frame = camera.current_frame()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    asset_id = None
    crop_path = ""
    if track_id:
        try:
            crop = camera.current_face_crop(track_id)
            crop_name = f"SNAP-{uuid.uuid4().hex[:10].upper()}-face.jpg"
            if not cv2.imwrite(str(SNAPSHOT_DIR / crop_name), crop):
                raise ValueError("Could not save face crop")
            crop_path = crop_name
            with camera.lock:
                asset_id = camera.track_assignments.get(track_id)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    # Save full raw frame; a separate display stream contains the reticle overlay.
    snap_id = "SNAP-" + uuid.uuid4().hex[:10].upper()
    filename = f"{snap_id}.jpg"
    if not cv2.imwrite(str(SNAPSHOT_DIR / filename), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92]):
        return jsonify({"ok": False, "error": "Could not save snapshot"}), 500
    with db_connect() as conn:
        conn.execute("INSERT INTO snapshots(id,created_at,track_id,asset_id,image_path,crop_path,notes) VALUES(?,?,?,?,?,?,?)", (snap_id, now_iso(), track_id, asset_id, filename, crop_path, notes))
    audit("SNAPSHOT_CREATED", f"{snap_id}; track={track_id}; asset={asset_id or 'none'}")
    return jsonify({"ok": True, "snapshot": {"id": snap_id, "created_at": now_iso(), "image_url": f"/media/snapshots/{filename}", "crop_url": f"/media/snapshots/{crop_path}" if crop_path else "", "track_id": track_id, "asset_id": asset_id}, "message": "Snapshot saved locally."}), 201


@app.get("/api/snapshots")
def list_snapshots():
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM snapshots ORDER BY created_at DESC LIMIT 100").fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["image_url"] = f"/media/snapshots/{Path(item['image_path']).name}"
        item["crop_url"] = f"/media/snapshots/{Path(item['crop_path']).name}" if item["crop_path"] else ""
        items.append(item)
    return jsonify({"snapshots": items})


@app.get("/media/profiles/<path:filename>")
def profile_media(filename: str):
    return send_from_directory(PROFILE_DIR, Path(filename).name, as_attachment=False, max_age=0)


@app.get("/media/snapshots/<path:filename>")
def snapshot_media(filename: str):
    return send_from_directory(SNAPSHOT_DIR, Path(filename).name, as_attachment=False, max_age=0)


@app.errorhandler(413)
def too_large(_exc):
    return jsonify({"ok": False, "error": "Upload too large. Local video uploads are limited to 512 MB."}), 413


@app.errorhandler(Exception)
def unhandled_error(exc: Exception):
    if isinstance(exc, HTTPException):
        return jsonify({"ok": False, "error": exc.description}), exc.code
    logging.exception("Unhandled application error")
    if app.debug:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": False, "error": "Unexpected local application error. Check logs/machine.log."}), 500


if __name__ == "__main__":
    # Explicit loopback bind: the UI is not exposed to the LAN by default.
    app.run(host="127.0.0.1", port=int(os.environ.get("MACHINE_PORT", "8765")), threaded=True, debug=False, use_reloader=False)
