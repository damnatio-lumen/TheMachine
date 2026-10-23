"""Unit tests for opt-in SFace matching without importing the Flask application."""
from __future__ import annotations

import ast
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
CAMERA_CLASS = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "CameraManager")
METHOD_NAMES = {"_match_biometric", "_update_biometric_matches"}
METHOD_NODES = [node for node in CAMERA_CLASS.body if isinstance(node, ast.FunctionDef) and node.name in METHOD_NAMES]
WRAPPER = ast.ClassDef(name="BiometricMethods", bases=[], keywords=[], body=METHOD_NODES, decorator_list=[])
MODULE = ast.fix_missing_locations(ast.Module(body=[WRAPPER], type_ignores=[]))
NAMESPACE = {
    "np": np,
    "now_iso": lambda: "2026-10-23T00:00:00+00:00",
}
exec(compile(MODULE, str(SOURCE), "exec"), NAMESPACE)
METHODS = NAMESPACE["BiometricMethods"]


class BiometricMatcherTests(unittest.TestCase):
    def make_engine(self, templates):
        engine = SimpleNamespace(
            biometric_enabled=True,
            biometric_models_ready=True,
            biometric_threshold=0.40,
            biometric_match_margin=0.05,
            biometric_template_cache=templates,
            biometric_links={},
            tracks={},
            asset_cache={asset_id: {"auto_expand_opt_in": False} for asset_id, _ in templates},
            frame_count=0,
        )
        engine._refresh_biometric_template_cache = lambda **_kwargs: None
        engine._refresh_asset_cache = lambda **_kwargs: None
        engine._propose_expansion_sample = lambda *_args, **_kwargs: None
        engine._match_biometric = types.MethodType(METHODS._match_biometric, engine)
        engine._update_biometric_matches = types.MethodType(METHODS._update_biometric_matches, engine)
        return engine

    def test_no_template_never_returns_a_profile(self):
        engine = self.make_engine([])
        asset_id, score = engine._match_biometric(np.asarray([1.0, 0.0], dtype=np.float32))
        self.assertIsNone(asset_id)
        self.assertEqual(score, -1.0)

    def test_clear_match_returns_best_asset(self):
        engine = self.make_engine([
            ("AST-GOOD", np.asarray([1.0, 0.0], dtype=np.float32)),
            ("AST-OTHER", np.asarray([0.0, 1.0], dtype=np.float32)),
        ])
        asset_id, score = engine._match_biometric(np.asarray([1.0, 0.0], dtype=np.float32))
        self.assertEqual(asset_id, "AST-GOOD")
        self.assertAlmostEqual(score, 1.0, places=5)

    def test_ambiguous_two_profile_scores_return_no_match(self):
        other = np.asarray([0.97, np.sqrt(1.0 - 0.97**2)], dtype=np.float32)
        engine = self.make_engine([
            ("AST-A", np.asarray([1.0, 0.0], dtype=np.float32)),
            ("AST-B", other),
        ])
        asset_id, score = engine._match_biometric(np.asarray([1.0, 0.0], dtype=np.float32))
        self.assertIsNone(asset_id)
        self.assertGreaterEqual(score, 0.97)

    def test_relock_requires_three_fresh_matching_feature_frames(self):
        engine = self.make_engine([("AST-ENROLLED", np.asarray([1.0, 0.0], dtype=np.float32))])
        engine.tracks["F-0001"] = {"detected": True, "embedding_frame": -1}
        row = {"track_id": "F-0001", "detected": True, "embedding": np.asarray([1.0, 0.0], dtype=np.float32)}
        for frame_id in (3, 6):
            engine.frame_count = frame_id
            engine.tracks["F-0001"]["embedding_frame"] = frame_id
            engine._update_biometric_matches([row])
            self.assertNotIn("F-0001", engine.biometric_links)
        engine.frame_count = 9
        engine.tracks["F-0001"]["embedding_frame"] = 9
        engine._update_biometric_matches([row])
        self.assertEqual(engine.biometric_links["F-0001"]["asset_id"], "AST-ENROLLED")

    def test_disabled_biometric_engine_never_relocks(self):
        engine = self.make_engine([("AST-ENROLLED", np.asarray([1.0, 0.0], dtype=np.float32))])
        engine.biometric_enabled = False
        engine.tracks["F-0001"] = {"detected": True, "embedding_frame": 3}
        engine.frame_count = 3
        engine._update_biometric_matches([{"track_id": "F-0001", "detected": True, "embedding": np.asarray([1.0, 0.0], dtype=np.float32)}])
        self.assertNotIn("F-0001", engine.biometric_links)


class ModelPackageMetadataTests(unittest.TestCase):
    def test_yunet_checksum_matches_published_model_reference(self):
        script = (ROOT / "scripts" / "download-biometric-models.ps1").read_text(encoding="utf-8")
        docs = (ROOT / "models" / "MODEL_SOURCES.md").read_text(encoding="utf-8")
        expected = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
        self.assertEqual(len(expected), 64)
        self.assertIn(expected.upper(), script.upper())
        self.assertIn(expected, docs.lower())

    def test_sface_checksum_is_consistent_between_script_and_docs(self):
        script = (ROOT / "scripts" / "download-biometric-models.ps1").read_text(encoding="utf-8")
        docs = (ROOT / "models" / "MODEL_SOURCES.md").read_text(encoding="utf-8")
        expected = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
        self.assertIn(expected.upper(), script.upper())
        self.assertIn(expected, docs.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
