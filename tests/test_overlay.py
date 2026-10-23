"""Regression tests for the exact dashed-rectangle method in app.py.

The method is loaded from the AST so this regression test can run without
starting Flask or creating the application's database.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "app.py"


def load_methods(*method_names):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    manager = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CameraManager")
    functions = [
        node for node in manager.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in method_names
    ]
    wrapper = ast.ClassDef(name="MethodsUnderTest", bases=[], keywords=[], body=functions, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    namespace = {"cv2": cv2, "np": np, "time": __import__("time")}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["MethodsUnderTest"]


METHODS = load_methods("_draw_dashed_rect", "_assign_tracks", "_filter_detections")
DRAW_DASHED_RECT = METHODS._draw_dashed_rect


class DashedRectangleTests(unittest.TestCase):
    def test_dashed_rectangle_draws_without_opencv_coordinate_error(self) -> None:
        frame = np.zeros((180, 240, 3), dtype=np.uint8)
        DRAW_DASHED_RECT(frame, 20, 15, 130, 145, (0, 220, 180))
        self.assertGreater(int(np.count_nonzero(frame)), 0)

    def test_normalizes_numpy_and_float_coordinates(self) -> None:
        frame = np.zeros((160, 220, 3), dtype=np.uint8)
        DRAW_DASHED_RECT(
            frame,
            np.int32(12),
            np.int64(16),
            111.0,
            120.0,
            (190, 190, 190),
            dash=11.7,
            thick=1.8,
        )
        self.assertGreater(int(np.count_nonzero(frame)), 0)


class TemporalTrackingTests(unittest.TestCase):
    def make_manager(self):
        from types import SimpleNamespace
        return SimpleNamespace(tracks={}, counter=0, track_assignments={}, frame_count=0)

    def test_new_track_needs_three_consistent_frames(self):
        manager = self.make_manager()
        detect = METHODS._assign_tracks
        self.assertEqual(detect(manager, [(100, 80, 80, 80)]), [])
        manager.frame_count += 1
        self.assertEqual(detect(manager, [(102, 81, 80, 80)]), [])
        manager.frame_count += 1
        rows = detect(manager, [(104, 82, 80, 80)])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["confirmed"])
        self.assertEqual(rows[0]["track_id"], "F-0001")

    def test_confirmed_track_keeps_id_across_short_detection_gap(self):
        manager = self.make_manager()
        detect = METHODS._assign_tracks
        detect(manager, [(100, 80, 80, 80)])
        manager.frame_count += 1
        detect(manager, [(103, 82, 80, 80)])
        manager.frame_count += 1
        first = detect(manager, [(106, 84, 80, 80)])[0]
        track_id = first["track_id"]
        manager.frame_count += 1
        gap = detect(manager, [])[0]
        self.assertEqual(gap["track_id"], track_id)
        self.assertFalse(gap["detected"])
        manager.frame_count += 1
        reacquired = detect(manager, [(111, 87, 80, 80)])[0]
        self.assertEqual(reacquired["track_id"], track_id)
        self.assertTrue(reacquired["detected"])

    def test_manual_asset_assignment_survives_short_detection_dropout(self):
        manager = self.make_manager()
        detect = METHODS._assign_tracks
        detect(manager, [(100, 80, 80, 80)])
        manager.frame_count += 1
        detect(manager, [(103, 82, 80, 80)])
        manager.frame_count += 1
        first = detect(manager, [(106, 84, 80, 80)])[0]
        track_id = first["track_id"]
        manager.track_assignments[track_id] = "AST-TEST1234"
        manager.frame_count += 1
        held = detect(manager, [])[0]
        self.assertEqual(held["track_id"], track_id)
        self.assertEqual(manager.track_assignments.get(track_id), "AST-TEST1234")
        manager.frame_count += 1
        re_locked = detect(manager, [(111, 87, 80, 80)])[0]
        self.assertEqual(re_locked["track_id"], track_id)
        self.assertEqual(manager.track_assignments.get(track_id), "AST-TEST1234")

    def test_box_size_smoothing_reduces_single_frame_size_jitter(self):
        manager = self.make_manager()
        detect = METHODS._assign_tracks
        observed_widths = []
        sizes = [100, 102, 118, 101, 119, 100, 121, 99, 118, 101, 120, 100]
        for idx, size in enumerate(sizes):
            manager.frame_count = idx
            rows = detect(manager, [(100 + (idx % 3), 80, size, size)])
            if rows:
                observed_widths.append(rows[0]["w"])
        self.assertGreaterEqual(len(observed_widths), 6)
        # The Haar detector can jump by ~20px between adjacent frames, while the
        # display reticle should respond gradually rather than resizing each time.
        self.assertLess(max(observed_widths[-7:]) - min(observed_widths[-7:]), 15)

    def test_filter_rejects_small_implausible_and_duplicate_boxes(self):
        filter_boxes = METHODS._filter_detections
        result = filter_boxes(
            [(10, 10, 30, 30), (50, 50, 80, 120), (200, 100, 80, 80), (204, 104, 78, 78)],
            640, 480,
        )
        self.assertEqual(len(result), 1)
        self.assertIn(result[0], [(200, 100, 80, 80), (204, 104, 78, 78)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
