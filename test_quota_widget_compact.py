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

    def test_calc_responsive_ring_size(self):
        # Normal mode
        self.assertEqual(quota_widget.calc_responsive_ring_size(1, compact=False), 58)
        self.assertEqual(quota_widget.calc_responsive_ring_size(2, compact=False), 58)
        self.assertEqual(quota_widget.calc_responsive_ring_size(3, compact=False), 50)
        self.assertEqual(quota_widget.calc_responsive_ring_size(4, compact=False), 44)
        # Compact mode
        self.assertEqual(quota_widget.calc_responsive_ring_size(1, compact=True), 48)
        self.assertEqual(quota_widget.calc_responsive_ring_size(2, compact=True), 48)
        self.assertEqual(quota_widget.calc_responsive_ring_size(3, compact=True), 40)
        self.assertEqual(quota_widget.calc_responsive_ring_size(4, compact=True), 36)

    def test_provider_definitions_integrity(self):
        for provider in ("agy", "claude1", "claude2", "codex"):
            self.assertIn(provider, quota_widget.PROVIDER_METRIC_DEFINITIONS)
            self.assertIn(provider, quota_widget.PROVIDER_TITLES)
            defs = quota_widget.PROVIDER_METRIC_DEFINITIONS[provider]
            self.assertTrue(len(defs) >= 2)
            for short_label, key, full_label in defs:
                self.assertTrue(isinstance(short_label, str) and short_label)
                self.assertTrue(isinstance(key, str) and key)
                self.assertTrue(isinstance(full_label, str) and full_label)


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

    def test_customizable_wheels_structure(self):
        self.assertIn("calc_responsive_ring_size", self.source)
        self.assertIn("open_metrics_config", self.source)
        self.assertIn("toggle_metric", self.source)
        self.assertIn("_rebuild_card_tiles", self.source)
        self.assertIn("_show_card_context_menu", self.source)
        self.assertIn("reset_card_metrics", self.source)
        self.assertIn("reset_all_metrics", self.source)

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


class QuotaWidgetInteractiveTests(unittest.TestCase):
    def test_toggle_and_rebuild_tiles(self):
        import tkinter as tk
        from quota_ui_state import default_ui_config
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = Path(tmp_dir) / "config.json"
            cfg = default_ui_config()
            cfg["ui_mode"] = "panel"
            save_ui_config(cfg, config_file)

            root = tk.Tk()
            root.withdraw()
            try:
                app = quota_widget.QuotaHUDApp(root, config_path=config_file)
                # claude1 initially has default metrics: five_hour, seven_day (2 metrics)
                self.assertEqual(list(app.controls["claude1"]["tiles"].keys()), ["five_hour", "seven_day"])
                # The ring size for 2 metrics is 58
                first_tile = app.controls["claude1"]["tiles"]["five_hour"]
                self.assertEqual(first_tile["indicator"].size, 58)

                # Add context metric (now 3 metrics)
                app.toggle_metric("claude1", "context")
                self.assertIn("context", app.controls["claude1"]["tiles"])
                self.assertEqual(len(app.controls["claude1"]["tiles"]), 3)
                # Ring size for 3 metrics is 50
                self.assertEqual(app.controls["claude1"]["tiles"]["context"]["indicator"].size, 50)

                # Add tokens metric (now 4 metrics)
                app.toggle_metric("claude1", "tokens")
                self.assertEqual(len(app.controls["claude1"]["tiles"]), 4)
                # Ring size for 4 metrics is 44
                self.assertEqual(app.controls["claude1"]["tiles"]["tokens"]["indicator"].size, 44)

                # Remove five_hour (now 3 metrics: seven_day, context, tokens)
                app.toggle_metric("claude1", "five_hour")
                self.assertNotIn("five_hour", app.controls["claude1"]["tiles"])
                self.assertEqual(len(app.controls["claude1"]["tiles"]), 3)
                self.assertEqual(app.controls["claude1"]["tiles"]["seven_day"]["indicator"].size, 50)

                # Reset to defaults
                app.reset_card_metrics("claude1")
                self.assertEqual(list(app.controls["claude1"]["tiles"].keys()), ["five_hour", "seven_day"])

                # Verify persistence in config_file
                persisted = load_ui_config(config_file)
                self.assertEqual(persisted["visible_metrics"]["claude1"], ["five_hour", "seven_day"])
            finally:
                root.destroy()

    def test_hud_app_claude_delegation(self):
        import tkinter as tk
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = Path(tmp_dir) / "config.json"
            save_ui_config({"ui_mode": "panel"}, config_file)
            root = tk.Tk()
            root.withdraw()
            try:
                app = quota_widget.QuotaHUDApp(root, config_path=config_file)
                app.profile_service = MagicMock()
                app.profile_service.get_active_email.return_value = "claude1@example.com"
                app.claude_providers[1] = MagicMock()
                app.claude_providers[2] = MagicMock()
                mock_snap = quota_widget.ProviderSnapshot(provider="claude1", status="ok", metrics={})
                app.claude_providers[1].get_snapshot.return_value = mock_snap
                app.claude_providers[2].get_snapshot.return_value = mock_snap

                snapshots = app.collect_snapshots()
                self.assertEqual(snapshots["claude1"], mock_snap)
                self.assertEqual(snapshots["claude2"], mock_snap)
                app.claude_providers[1].get_snapshot.assert_called_once()

                app.switch_claude1()
                app.profile_service.switch_account_number.assert_called_with(1)
                app.claude_providers[1].trigger_refresh.assert_called()

                app.switch_claude2()
                app.profile_service.switch_account_number.assert_called_with(2)
                app.claude_providers[2].trigger_refresh.assert_called()
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
