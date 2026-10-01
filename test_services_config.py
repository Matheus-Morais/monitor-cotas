"""Unit tests for services/config_manager.py."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from services.config_manager import (
    DEFAULT_CONFIG,
    ConfigManager,
    normalize_config,
    resolve_config_path,
)


class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "config.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_config_structure(self):
        cfg = normalize_config({})
        self.assertEqual(cfg["ui_mode"], "panel")
        self.assertEqual(cfg["avatar_size"], 72)
        self.assertIn("panel_geometry", cfg)
        self.assertGreaterEqual(cfg["panel_geometry"]["width"], 240)
        self.assertGreaterEqual(cfg["panel_geometry"]["height"], 180)
        self.assertIn("visible_metrics", cfg)
        self.assertIn("agy", cfg["visible_metrics"])

    def test_provider_label_mode(self):
        self.assertEqual(normalize_config({})["provider_label_mode"], "both")
        self.assertEqual(normalize_config({"provider_label_mode": "logo"})["provider_label_mode"], "logo")
        self.assertEqual(normalize_config({"provider_label_mode": "xyz"})["provider_label_mode"], "both")

    def test_metrics_align(self):
        self.assertEqual(normalize_config({})["metrics_align"], "center")
        self.assertEqual(normalize_config({"metrics_align": "left"})["metrics_align"], "left")
        self.assertEqual(normalize_config({"metrics_align": "top"})["metrics_align"], "center")

    def test_normalize_enforces_boundaries(self):
        raw = {
            "ui_mode": "invalid_mode",
            "avatar_size": 10,  # Below minimum 32
            "panel_geometry": {"width": 100, "height": 50},  # Below min 240x180
            "visible_metrics": {
                "agy": ["gemini_5h", "non_existent_metric"],
            },
        }
        cfg = normalize_config(raw)
        self.assertEqual(cfg["ui_mode"], "panel")
        self.assertEqual(cfg["avatar_size"], 72)  # Fallback to default
        self.assertEqual(cfg["panel_geometry"]["width"], 384)  # Fallback
        self.assertEqual(cfg["panel_geometry"]["height"], 581)  # Fallback
        self.assertIn("gemini_5h", cfg["visible_metrics"]["agy"])
        self.assertNotIn("non_existent_metric", cfg["visible_metrics"]["agy"])

    def test_save_and_load_roundtrip(self):
        manager = ConfigManager(self.config_path)
        self.assertFalse(self.config_path.exists())

        # Save updates
        manager.set("compact_mode", True)
        self.assertTrue(self.config_path.exists())

        # Verify raw file is valid JSON
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertTrue(data["compact_mode"])

        # Create new manager pointing to same path
        manager2 = ConfigManager(self.config_path)
        self.assertTrue(manager2.get("compact_mode"))

    def test_update_merges_data(self):
        manager = ConfigManager(self.config_path)
        manager.update({"ui_mode": "avatar", "avatar_size": 80})
        self.assertEqual(manager.get("ui_mode"), "avatar")
        self.assertEqual(manager.get("avatar_size"), 80)
        # Ensure other defaults are preserved
        self.assertIn("panel_geometry", manager.data)

    def test_resolve_config_path_explicit(self):
        explicit = Path(self.temp_dir) / "custom.json"
        resolved = resolve_config_path(explicit)
        self.assertEqual(resolved, explicit)


if __name__ == "__main__":
    unittest.main()
