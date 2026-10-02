"""Unit tests specifically targeting Avatar Alert configuration and API behaviour."""

import tempfile
import unittest
from pathlib import Path

from quota_webview_app import QuotaAPI
from services.config_manager import ConfigManager, normalize_config
from test_webview_api import MockApp


class TestAvatarAlerts(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.temp_file.close()
        self.mock_app = MockApp(self.temp_file.name)
        self.api = QuotaAPI(self.mock_app)

    def test_default_avatar_alert_structure(self):
        cfg = normalize_config({})
        self.assertIn("avatar_alert", cfg)
        self.assertTrue(cfg["avatar_alert"]["enabled"])
        self.assertEqual(cfg["avatar_alert"]["threshold_pct"], 15)
        self.assertTrue(cfg["avatar_alert"]["pulse_animation"])

    def test_custom_avatar_alert_persistence(self):
        manager = ConfigManager(self.temp_file.name)
        manager.set("avatar_alert", {
            "enabled": True,
            "threshold_pct": 20,
            "pulse_animation": False,
        })
        self.assertEqual(manager.get("avatar_alert")["threshold_pct"], 20)
        self.assertFalse(manager.get("avatar_alert")["pulse_animation"])

        # Reload from disk
        manager2 = ConfigManager(self.temp_file.name)
        self.assertEqual(manager2.get("avatar_alert")["threshold_pct"], 20)
        self.assertFalse(manager2.get("avatar_alert")["pulse_animation"])

    def test_api_set_avatar_alert_config(self):
        res1 = self.api.set_avatar_alert_config("threshold_pct", 25)
        self.assertEqual(res1["avatar_alert"]["threshold_pct"], 25)

        res2 = self.api.set_avatar_alert_config("pulse_animation", False)
        self.assertFalse(res2["avatar_alert"]["pulse_animation"])

        res3 = self.api.set_avatar_alert_config("enabled", False)
        self.assertFalse(res3["avatar_alert"]["enabled"])

        # Clamping invalid values
        res4 = self.api.set_avatar_alert_config("threshold_pct", 999)
        self.assertEqual(res4["avatar_alert"]["threshold_pct"], 50)

        res5 = self.api.set_avatar_alert_config("threshold_pct", 1)
        self.assertEqual(res5["avatar_alert"]["threshold_pct"], 5)


if __name__ == "__main__":
    unittest.main()
