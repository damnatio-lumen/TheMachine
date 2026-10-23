"""Local video upload API tests. Uses a tiny synthetic MJPEG AVI fixture."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import importlib.util

import cv2
import numpy as np

HAS_FLASK = importlib.util.find_spec("flask") is not None
if HAS_FLASK:
    import app as machine_app


@unittest.skipUnless(HAS_FLASK, "Flask is not installed in the build environment; API tests run after scripts/install.ps1")
class VideoSourceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_db = machine_app.DB_PATH
        self.old_video_dir = machine_app.VIDEO_DIR
        machine_app.DB_PATH = self.root / "test.sqlite3"
        machine_app.VIDEO_DIR = self.root / "videos"
        machine_app.VIDEO_DIR.mkdir(parents=True)
        machine_app.init_db()
        machine_app.app.config.update(TESTING=True)
        self.client = machine_app.app.test_client()

    def tearDown(self):
        machine_app.DB_PATH = self.old_db
        machine_app.VIDEO_DIR = self.old_video_dir
        machine_app.app.config.update(TESTING=False)
        self.temp.cleanup()

    def make_avi(self) -> bytes:
        path = self.root / "fixture.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 8.0, (96, 96))
        if not writer.isOpened():
            self.skipTest("OpenCV MJPEG VideoWriter codec unavailable in this environment")
        for index in range(8):
            frame = np.zeros((96, 96, 3), dtype=np.uint8)
            cv2.rectangle(frame, (10 + index, 10), (65 + index, 65), (0, 220, 160), -1)
            writer.write(frame)
        writer.release()
        return path.read_bytes()

    def test_upload_video_saves_valid_local_source_and_lists_it(self):
        payload = self.make_avi()
        response = self.client.post(
            "/api/video/upload",
            data={"video": (io.BytesIO(payload), "test-feed.avi")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        record = response.get_json()["video"]
        self.assertEqual(record["original_name"], "test-feed.avi")
        stored = list(machine_app.VIDEO_DIR.glob("VID-*.avi"))
        self.assertEqual(len(stored), 1)
        self.assertGreater(stored[0].stat().st_size, 0)
        listed = self.client.get("/api/videos").get_json()["videos"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], record["id"])

    def test_unsupported_extension_is_rejected(self):
        response = self.client.post(
            "/api/video/upload",
            data={"video": (io.BytesIO(b"not-a-video"), "sample.exe")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported video extension", response.get_json()["error"])

    def test_start_requires_a_known_source_id(self):
        response = self.client.post("/api/video/start", json={"source_id": "VID-DOES-NOT-EXIST"})
        self.assertEqual(response.status_code, 404)

    def test_start_passes_only_database_resolved_path_to_manager(self):
        payload = self.make_avi()
        response = self.client.post(
            "/api/video/upload",
            data={"video": (io.BytesIO(payload), "test-feed.avi")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        source_id = response.get_json()["video"]["id"]
        with patch.object(machine_app.camera, "start_video", return_value=(True, "fixture started")) as start:
            started = self.client.post("/api/video/start", json={"source_id": source_id})
        self.assertEqual(started.status_code, 200)
        path_arg, label_arg = start.call_args.args
        self.assertTrue(Path(path_arg).is_file())
        self.assertEqual(label_arg, "test-feed.avi")


if __name__ == "__main__":
    unittest.main()
