"""Unit tests for telemetry providers."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from providers.antigravity import AntigravityProvider
from providers.claude import ClaudeProvider
from providers.codex import CodexProvider


class TestProviders(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.base_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_antigravity_provider_missing_file(self):
        prov = AntigravityProvider(status_path=self.base_path / "nonexistent.json")
        self.assertFalse(prov.is_available())
        snapshot = prov.collect()
        self.assertEqual(snapshot.status, "unavailable")

    def test_antigravity_provider_valid_file(self):
        status_file = self.base_path / "agy_status.json"
        status_file.write_text(
            json.dumps({
                "model": {"display_name": "gemini-2.5-pro"},
                "quota": {
                    "gemini-5h": {"remaining_fraction": 0.825, "reset_time": 1800},
                    "gemini-weekly": {"remaining_fraction": 0.95, "reset_time": 86400},
                },
            }),
            encoding="utf-8",
        )
        prov = AntigravityProvider(status_path=status_file)
        self.assertTrue(prov.is_available())
        snapshot = prov.collect()
        self.assertEqual(snapshot.status, "ok")
        self.assertIn("gemini_5h", snapshot.metrics)
        self.assertEqual(snapshot.metrics["gemini_5h"].remaining_pct, 82)

    def test_claude_provider_fallback_and_active_account(self):
        profile_file = self.base_path / ".claude-1.json"
        profile_file.write_text(
            json.dumps({
                "oauthAccount": {"emailAddress": "dev1@test.com"},
                "model": {"display_name": "claude-3-7-sonnet"},
                "rate_limits": {
                    "five_hour": {"used_percentage": 20},
                },
            }),
            encoding="utf-8",
        )

        active_file = self.base_path / ".claude.json"
        active_file.write_text(
            json.dumps({
                "oauthAccount": {"emailAddress": "dev1@test.com"},
                "model": {"display_name": "claude-3-7-sonnet-freshest"},
                "rate_limits": {
                    "five_hour": {"used_percentage": 15},
                },
            }),
            encoding="utf-8",
        )

        # Active email matches profile email -> should read active_file for freshest data
        prov = ClaudeProvider(
            account_num=1,
            profile_path=profile_file,
            active_path=active_file,
            active_email_getter=lambda: "dev1@test.com",
        )
        self.assertTrue(prov.is_available())
        snapshot = prov.collect()
        self.assertEqual(snapshot.account, "dev1@test.com")
        self.assertEqual(snapshot.model, "claude-3-7-sonnet-freshest")

    def test_claude_provider_different_account_does_not_override(self):
        profile_file = self.base_path / ".claude-2.json"
        profile_file.write_text(
            json.dumps({
                "oauthAccount": {"emailAddress": "dev2@test.com"},
                "model": {"display_name": "claude-3-5-sonnet"},
                "rate_limits": {
                    "five_hour": {"used_percentage": 50},
                },
            }),
            encoding="utf-8",
        )

        active_file = self.base_path / ".claude.json"
        active_file.write_text(
            json.dumps({
                "oauthAccount": {"emailAddress": "dev1@test.com"},
                "model": {"display_name": "claude-active-model"},
                "rate_limits": {
                    "five_hour": {"used_percentage": 10},
                },
            }),
            encoding="utf-8",
        )

        # Active email is dev1, but this provider is dev2 -> should NOT read active_file
        prov = ClaudeProvider(
            account_num=2,
            profile_path=profile_file,
            active_path=active_file,
            active_email_getter=lambda: "dev1@test.com",
        )
        snapshot = prov.collect()
        self.assertEqual(snapshot.account, "dev2@test.com")
        self.assertEqual(snapshot.model, "claude-3-5-sonnet")

    def test_codex_provider_missing(self):
        prov = CodexProvider(
            history_db=self.base_path / "thread.sqlite",
            state_db=self.base_path / "state.sqlite",
            rollouts_dir=self.base_path / "rollouts",
        )
        self.assertFalse(prov.is_available())
        snapshot = prov.collect()
        self.assertEqual(snapshot.status, "unavailable")


if __name__ == "__main__":
    unittest.main()
