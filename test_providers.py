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

    def test_claude_provider_merges_statusline_context_and_model(self):
        active_file = self.base_path / ".claude.json"
        active_file.write_text(
            json.dumps({
                "oauthAccount": {"emailAddress": "active@domain.com", "organizationType": "claude_team"},
                "cachedUsageUtilization": {
                    "utilization": {
                        "five_hour": {"utilization": 10, "resets_at": 20000},
                        "seven_day": {"utilization": 20, "resets_at": 30000},
                    }
                }
            }),
            encoding="utf-8",
        )

        status_file = self.base_path / "claude-statusline.json"
        status_file.write_text(
            json.dumps({
                "model": {"display_name": "Opus 5.5 (Live)"},
                "context_window": {"remaining_percentage": 78},
                "rate_limits": {
                    "five_hour": {"used_percentage": 12, "resets_at": 20000},
                    "seven_day": {"used_percentage": 22, "resets_at": 30000},
                },
                "cost": {"total_cost_usd": 4.56},
            }),
            encoding="utf-8",
        )

        prov = ClaudeProvider(
            account_num=1,
            profile_path=self.base_path / ".claude-1.json",
            active_path=active_file,
            status_path=status_file,
            active_email_getter=lambda: "active@domain.com",
        )

        snap = prov.collect()
        self.assertEqual(snap.status, "ok")
        self.assertEqual(snap.account, "active@domain.com")
        self.assertEqual(snap.plan, "Team")
        self.assertEqual(snap.model, "Opus 5.5 (Live)")
        self.assertEqual(snap.metrics["context"].remaining_pct, 78)
        self.assertEqual(snap.metadata.get("cost_usd"), 4.56)

    def test_claude_provider_availability_rules(self):
        # Account 2 requires .claude-2.json to exist
        prov2 = ClaudeProvider(
            account_num=2,
            profile_path=self.base_path / ".claude-2.json",
            active_path=self.base_path / ".claude.json",
        )
        self.assertFalse(prov2.is_available())

        # Account 1 is available if active_path exists
        active_file = self.base_path / ".claude.json"
        active_file.write_text("{}", encoding="utf-8")
        prov1 = ClaudeProvider(
            account_num=1,
            profile_path=self.base_path / ".claude-1.json",
            active_path=active_file,
        )
        self.assertTrue(prov1.is_available())

    def test_claude_provider_trigger_refresh(self):
        prov = ClaudeProvider(account_num=1)
        self.assertTrue(prov.trigger_refresh())


if __name__ == "__main__":
    unittest.main()
