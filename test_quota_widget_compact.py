import json
import tempfile
import unittest
from pathlib import Path

import quota_widget_compact as quota_widget
from quota_ui_state import load_ui_config, save_ui_config


class QuotaWidgetLogicTests(unittest.TestCase):
    def test_percentage_to_arc(self):
        self.assertEqual(quota_widget.percentage_to_arc(100), -360)
        self.assertEqual(quota_widget.percentage_to_arc(50), -180)
        self.assertEqual(quota_widget.percentage_to_arc(0), 0)
        self.assertIsNone(quota_widget.percentage_to_arc(None))

    def test_metric_color_ranges(self):
        self.assertEqual(quota_widget.metric_color(None), quota_widget.TEXT_MUTED)
        self.assertEqual(quota_widget.metric_color(0), quota_widget.COLOR_RED)
        self.assertEqual(quota_widget.metric_color(10), quota_widget.COLOR_RED)
        self.assertEqual(quota_widget.metric_color(11), quota_widget.COLOR_YELLOW)
        self.assertEqual(quota_widget.metric_color(30), quota_widget.COLOR_YELLOW)
        self.assertEqual(quota_widget.metric_color(31), quota_widget.COLOR_GREEN)

    def test_config_compatibility_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertEqual(load_ui_config(path)["ui_mode"], "avatar")
            path.write_text(json.dumps({"mode": "panel", "avatarSize": 80, "position": {"x": 12, "y": 24}, "geometry": {"x": 4, "y": 8, "width": 640, "height": 480}, "taskbar": True}), encoding="utf-8")
            legacy = load_ui_config(path)
            self.assertEqual(legacy["ui_mode"], "panel")
            self.assertEqual(legacy["avatar_size"], 80)
            self.assertEqual(legacy["avatar_position"], {"x": 12, "y": 24})
            self.assertEqual(legacy["panel_geometry"]["width"], 640)
            self.assertTrue(legacy["show_in_taskbar"])

            saved = save_ui_config({"ui_mode": "avatar", "avatar_size": 76, "avatar_position": {"x": 10, "y": 20}, "panel_geometry": {"x": 30, "y": 40, "width": 700, "height": 500}, "show_in_taskbar": False}, path)
            self.assertEqual(load_ui_config(path), saved)
            self.assertFalse(load_ui_config(path)["compact_mode"])

    def test_click_drag_threshold(self):
        self.assertTrue(quota_widget.release_is_click(10, 10, 16, 16))
        self.assertFalse(quota_widget.release_is_click(10, 10, 17, 10))
        self.assertFalse(quota_widget.release_is_click(10, 10, 10, 17))

    def test_visible_countdown_is_compact(self):
        self.assertEqual(quota_widget.visible_countdown(3 * 3600 + 7 * 60 + 45, now=0), "3h 07m")
        self.assertEqual(quota_widget.visible_countdown(50 * 60 + 34, now=0), "50m")
        self.assertEqual(quota_widget.visible_countdown(45, now=0), "45s")
        self.assertEqual(quota_widget.visible_countdown(None), "")


class QuotaWidgetStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(quota_widget.__file__).read_text(encoding="utf-8")

    def test_compact_ui_structure(self):
        self.assertIn('self.mode = "avatar"', self.source)
        self.assertIn('mode == "panel"', self.source)
        self.assertIn("class CircularProgressWidget", self.source)
        self.assertNotIn("ProgressBarWidget", self.source)
        self.assertIn("toggle_compact", self.source)
        self.assertIn("toggle_card", self.source)
        self.assertIn('root.attributes("-alpha", 1.0)', self.source)
        self.assertIn("enable_windows_dpi_awareness", self.source)

    def test_panel_labels_and_claude_actions(self):
        for label in ("5h", "7d", "ctx", "tokens"):
            self.assertIn(f'("{label}"', self.source)
        self.assertIn("switch_claude1", self.source)
        self.assertIn("switch_claude2", self.source)
        self.assertIn("class Tooltip", self.source)

    def test_single_collection_and_fast_start(self):
        self.assertEqual(self.source.count("self.collect_snapshots()"), 1)
        self.assertIn("self.root.after(0, self.update_data)", self.source)
        self.assertIn("acquire_instance_mutex", self.source)
        self.assertIn("pystray", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
