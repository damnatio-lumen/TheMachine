"""Smoke test the source-selection method without importing Flask."""
from __future__ import annotations

import ast
import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
CAMERA_CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "CameraManager")
START_VIDEO_NODE = next(n for n in CAMERA_CLASS.body if isinstance(n, ast.FunctionDef) and n.name == "start_video")
WRAPPER = ast.ClassDef(name="VideoStartMethod", bases=[], keywords=[], body=[START_VIDEO_NODE], decorator_list=[])
MODULE = ast.fix_missing_locations(ast.Module(body=[WRAPPER], type_ignores=[]))


class NoRunThread:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
    def start(self):
        return None


NAMESPACE = {
    "Path": Path,
    "cv2": cv2,
    "time": time,
    "threading": SimpleNamespace(Thread=NoRunThread),
    "logging": logging,
    "audit": lambda *_args, **_kwargs: None,
}
exec(compile(MODULE, str(ROOT / "app.py"), "exec"), NAMESPACE)
START_VIDEO = NAMESPACE["VideoStartMethod"].start_video


class VideoStartTests(unittest.TestCase):
    def test_start_video_opens_local_clip_and_uses_source_fps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 8.0, (96, 96))
            if not writer.isOpened():
                self.skipTest("MJPEG VideoWriter codec unavailable")
            for frame_no in range(8):
                frame = np.zeros((96, 96, 3), dtype=np.uint8)
                cv2.circle(frame, (20 + frame_no, 45), 10, (200, 210, 220), -1)
                writer.write(frame)
            writer.release()
            manager = SimpleNamespace(
                lock=threading.RLock(), running=False, cap=None, prefetched_frame=None,
                backend_name="", source_type="CAMERA", source_label="LOCAL CAMERA", video_eof=False,
                video_frame_interval=1 / 25, frame_count=0, consecutive_read_failures=0,
                last_frame_at="", jpeg=None, last_error="", faces=[], tracks={},
                track_assignments={}, thread=None, counter=0, _run_loop_guarded=lambda: None,
            )
            ok, message = START_VIDEO(manager, path, "fixture.avi")
            self.assertTrue(ok, message)
            self.assertEqual(manager.source_type, "VIDEO")
            self.assertEqual(manager.source_label, "fixture.avi")
            self.assertIsNotNone(manager.prefetched_frame)
            self.assertAlmostEqual(manager.video_frame_interval, 1 / 8, delta=0.03)
            manager.cap.release()


if __name__ == "__main__":
    unittest.main()
