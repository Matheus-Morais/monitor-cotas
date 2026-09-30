import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import quota_core


class QuotaCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_json(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_missing_sources_are_not_green(self):
        agy = quota_core.load_agy_snapshot(self.root / "missing-agy.json")
        claude = quota_core.load_claude_snapshot(self.root / "missing-claude.json")
        codex = quota_core.load_codex_snapshot(self.root / "missing-history.db", self.root / "missing-state.db")

        self.assertEqual(agy.status, quota_core.UNAVAILABLE)
        self.assertEqual(claude.status, quota_core.UNAVAILABLE)
        self.assertEqual(codex.status, quota_core.UNAVAILABLE)
        self.assertEqual(agy.metrics, {})
        self.assertEqual(claude.metrics, {})
        self.assertEqual(codex.metrics, {})

    def test_malformed_sources_are_not_green(self):
        path = self.root / "bad.json"
        path.write_text("{not-json", encoding="utf-8")

        agy = quota_core.load_agy_snapshot(path)
        claude = quota_core.load_claude_snapshot(path)

        self.assertEqual(agy.status, quota_core.ERROR)
        self.assertEqual(claude.status, quota_core.ERROR)

    def test_antigravity_snapshot(self):
        path = self.write_json("agy.json", {
            "plan_tier": "Google AI Pro",
            "model": {"display_name": "Gemini"},
            "quota": {
                "gemini-5h": {"remaining_fraction": 0.73, "reset_time": 1234},
                "3p-5h": {"remaining_fraction": 0.2},
            },
        })

        snapshot = quota_core.load_agy_snapshot(path)

        self.assertEqual(snapshot.status, quota_core.OK)
        self.assertEqual(snapshot.metrics["gemini_5h"].remaining_pct, 73)
        self.assertEqual(snapshot.metrics["gemini_5h"].reset_at, 1234)
        self.assertIsNone(snapshot.metrics["gemini_weekly"].remaining_pct)

    def test_claude_rate_limits_snapshot(self):
        path = self.write_json("claude.json", {
            "model": {"display_name": "Sonnet"},
            "rate_limits": {
                "five_hour": {"used_percentage": 15, "resets_at": 2222},
                "seven_day": {"used_percentage": 40, "resets_at": 3333},
            },
            "context_window": {"remaining_percentage": 71},
        })

        snapshot = quota_core.load_claude_snapshot(path)

        self.assertEqual(snapshot.metrics["five_hour"].remaining_pct, 85)
        self.assertEqual(snapshot.metrics["five_hour"].reset_at, 2222)
        self.assertEqual(snapshot.metrics["seven_day"].remaining_pct, 60)
        self.assertEqual(snapshot.metrics["context"].remaining_pct, 71)

    def test_claude_cached_usage_snapshot(self):
        path = self.write_json("claude-cached.json", {
            "oauthAccount": {"emailAddress": "user@example.com"},
            "cachedUsageUtilization": {
                "utilization": {
                    "five_hour": {"utilization": 12, "resets_at": 4444},
                    "seven_day": {"utilization": 31, "resets_at": 5555},
                }
            },
        })

        snapshot = quota_core.load_claude_snapshot(path)

        self.assertEqual(snapshot.account, "user@example.com")
        self.assertEqual(snapshot.metrics["five_hour"].remaining_pct, 88)
        self.assertEqual(snapshot.metrics["seven_day"].remaining_pct, 69)
        self.assertIsNone(snapshot.metrics["context"].remaining_pct)

    def test_claude_profile_without_usage_keeps_identity(self):
        path = self.write_json("claude-no-cache.json", {
            "oauthAccount": {
                "emailAddress": "second@example.com",
                "accountUuid": "account-2",
                "organizationUuid": "org-2",
            }
        })

        snapshot = quota_core.load_claude_snapshot(path)

        self.assertEqual(snapshot.status, quota_core.UNAVAILABLE)
        self.assertEqual(snapshot.account, "second@example.com")
        self.assertEqual(snapshot.metadata["account_uuid"], "account-2")
        self.assertFalse(snapshot.metadata["telemetry_available"])

    def test_codex_missing_databases(self):
        snapshot = quota_core.load_codex_snapshot(self.root / "history.db", self.root / "state.db")
        self.assertEqual(snapshot.status, quota_core.UNAVAILABLE)
        self.assertFalse(snapshot.estimated)

    def test_codex_snapshot_is_estimated(self):
        history = self.root / "history.db"
        state = self.root / "state.db"
        with closing(sqlite3.connect(history)) as conn:
            conn.execute("CREATE TABLE thread_turns (started_at REAL)")
            conn.execute("INSERT INTO thread_turns VALUES (?)", (1000,))
            conn.commit()
        with closing(sqlite3.connect(state)) as conn:
            conn.execute("CREATE TABLE threads (tokens_used INTEGER, updated_at_ms INTEGER)")
            conn.execute("INSERT INTO threads VALUES (?, ?)", (2_000_000, 1_000_000))
            conn.commit()

        snapshot = quota_core.load_codex_snapshot(history, state, now=2000)

        self.assertEqual(snapshot.status, quota_core.OK)
        self.assertTrue(snapshot.estimated)
        self.assertTrue(snapshot.metrics["five_hour"].estimated)
        self.assertEqual(snapshot.metrics["five_hour"].detail, "1/50 turnos")

    def test_codex_snapshot_uses_server_rate_limits_from_rollout(self):
        sessions = self.root / "sessions" / "2026" / "09" / "30"
        sessions.mkdir(parents=True)
        rollout = sessions / "rollout-test.jsonl"
        rollout.write_text(json.dumps({
            "timestamp": "2026-09-30T12:00:00Z",
            "payload": {
                "rate_limits": {
                    "limit_id": "codex",
                    "plan_type": "plus",
                    "primary": {"used_percent": 7, "window_minutes": 300, "resets_at": 20000},
                    "secondary": {"used_percent": 20, "window_minutes": 10080, "resets_at": 30000},
                    "credits": {"has_credits": False, "balance": "0"},
                }
            }
        }) + "\n", encoding="utf-8")

        snapshot = quota_core.load_codex_snapshot(
            self.root / "missing-history.db",
            self.root / "missing-state.db",
            now=10000,
            rollouts_dir=self.root / "sessions",
        )

        self.assertEqual(snapshot.status, quota_core.OK)
        self.assertFalse(snapshot.estimated)
        self.assertEqual(snapshot.metrics["five_hour"].remaining_pct, 93)
        self.assertEqual(snapshot.metrics["seven_day"].remaining_pct, 80)
        self.assertEqual(snapshot.metrics["five_hour"].reset_at, 20000)
        self.assertTrue(snapshot.metadata["rate_limit_source"].endswith("rollout-test.jsonl"))

    def test_presentations_use_shared_core(self):
        import quota_monitor
        import quota_widget
        import collector

        self.assertIs(quota_monitor.format_countdown, quota_core.format_countdown)
        self.assertIs(quota_widget.format_countdown, quota_core.format_countdown)
        self.assertIs(quota_monitor.QuotaCollector, collector.QuotaCollector)
        self.assertIs(quota_widget.QuotaCollector, collector.QuotaCollector)

    def test_format_countdown_is_shared_and_deterministic(self):
        self.assertEqual(quota_core.format_countdown(3661, now=0), "1h 01m 01s")
        self.assertEqual(quota_core.format_countdown(59, now=60), "Resetado")


if __name__ == "__main__":
    unittest.main(verbosity=2)
