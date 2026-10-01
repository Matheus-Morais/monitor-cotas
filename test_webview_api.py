"""Headless unit tests for QuotaAPI (JS Bridge)."""

import tempfile
import unittest
from unittest.mock import MagicMock

from quota_webview_app import QuotaAPI
from services.config_manager import ConfigManager


class MockApp:
    def __init__(self, temp_config_path):
        self.config_manager = ConfigManager(temp_config_path)
        self.telemetry_service = MagicMock()
        self.telemetry_service.get_latest.return_value = {
            "agy": {"status": "ok", "metrics": {}},
        }
        self.telemetry_service.force_refresh.return_value = {
            "agy": {"status": "ok", "metrics": {}},
        }
        self.profile_service = MagicMock()
        self.profile_service.switch_account_number.return_value = True
        self.history_store = MagicMock()
        self.history_store.query_history.return_value = {
            "provider": "agy",
            "metric": "gemini_5h",
            "points": [{"t": 1000.0, "pct": 95, "status": "ok"}],
            "burn_rate_pct_hr": 2.5,
            "eta_seconds": 3600,
        }
        self.history_store.get_available_metrics.return_value = [
            {"provider": "agy", "metric": "gemini_5h"}
        ]

        self.is_pinned = True
        self.window = None
        self.mode = "panel"

    def toggle_mode(self):
        self.mode = "avatar" if self.mode == "panel" else "panel"
        return {"mode": self.mode}

    def set_mode(self, mode):
        self.mode = mode
        return {"mode": self.mode}

    def start_resize(self, direction):
        pass

    def manual_resize(self, width, height, x=None, y=None):
        return {"width": max(240, width), "height": max(180, height)}

    def close(self):
        pass


class TestQuotaAPI(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.temp_file.close()
        self.mock_app = MockApp(self.temp_file.name)
        self.api = QuotaAPI(self.mock_app)

    def test_init_returns_snapshots_and_config(self):
        res = self.api.init()
        self.assertIn("snapshots", res)
        self.assertIn("config", res)
        self.assertIn("agy", res["snapshots"])

    def test_get_snapshots(self):
        res = self.api.get_snapshots()
        self.assertIn("agy", res)

    def test_toggle_metric(self):
        # Initial: agy has gemini_5h
        cfg = self.api.toggle_metric("agy", "gemini_5h")
        self.assertNotIn("gemini_5h", cfg["visible_metrics"]["agy"])

        # Toggle back on
        cfg2 = self.api.toggle_metric("agy", "gemini_5h")
        self.assertIn("gemini_5h", cfg2["visible_metrics"]["agy"])

    def test_reset_all_metrics(self):
        self.api.toggle_metric("agy", "gemini_5h")
        cfg = self.api.reset_all_metrics()
        self.assertIn("gemini_5h", cfg["visible_metrics"]["agy"])

    def test_save_collapsed(self):
        self.api.save_collapsed({"agy": True, "claude1": False})
        self.assertTrue(self.mock_app.config_manager.get("collapsed_cards")["agy"])

    def test_switch_claude(self):
        success = self.api.switch_claude(1)
        self.assertTrue(success)
        self.mock_app.profile_service.switch_account_number.assert_called_with(1)
        self.mock_app.telemetry_service.collect.assert_called()

    def test_force_refresh(self):
        res = self.api.force_refresh()
        self.mock_app.telemetry_service.force_refresh.assert_called()
        self.assertIn("agy", res)

    def test_toggle_pin(self):
        self.assertTrue(self.mock_app.is_pinned)
        new_state = self.api.toggle_pin()
        self.assertFalse(new_state)
        self.assertFalse(self.mock_app.is_pinned)

    def test_manual_resize_clamps_minimum(self):
        res = self.api.manual_resize(100, 100)
        self.assertEqual(res["width"], 240)
        self.assertEqual(res["height"], 180)

    def test_get_history(self):
        res = self.api.get_history("agy", "gemini_5h", "6h")
        self.mock_app.history_store.query_history.assert_called_with(
            provider="agy",
            metric="gemini_5h",
            range_seconds=21600.0,
            max_points=120,
        )
        self.assertEqual(res["burn_rate_pct_hr"], 2.5)

    def test_get_history_providers(self):
        res = self.api.get_history_providers()
        self.mock_app.history_store.get_available_metrics.assert_called_once()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["provider"], "agy")


if __name__ == "__main__":
    unittest.main()
