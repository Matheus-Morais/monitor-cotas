"""Unit tests for HistoryStore analytics, downsampling, burn rate, and TelemetryService integration."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from history import HistoryStore
from quota_core import Metric, ProviderSnapshot
from services.config_manager import ConfigManager
from services.telemetry import TelemetryService


class TestHistoryAnalytics(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_history.sqlite"
        self.store = HistoryStore(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_serialized_snapshots_change_driven(self):
        t0 = 1000.0
        snaps1 = {
            "agy": {
                "status": "ok",
                "source_age_seconds": 5,
                "metrics": {
                    "gemini_5h": {
                        "remaining_pct": 90,
                        "reset_at": "2026-10-01T15:00:00Z",
                        "detail": "90/100",
                        "estimated": False,
                    }
                },
            }
        }
        # First record: should insert 1 row
        n1 = self.store.record_serialized_snapshots(snaps1, collected_at=t0)
        self.assertEqual(n1, 1)

        # Same values within heartbeat: should NOT insert
        n2 = self.store.record_serialized_snapshots(snaps1, collected_at=t0 + 10.0, force_heartbeat_seconds=60.0)
        self.assertEqual(n2, 0)

        # Value changed: should insert immediately
        snaps2 = {
            "agy": {
                "status": "ok",
                "source_age_seconds": 5,
                "metrics": {
                    "gemini_5h": {
                        "remaining_pct": 85,
                        "reset_at": "2026-10-01T15:00:00Z",
                        "detail": "85/100",
                        "estimated": False,
                    }
                },
            }
        }
        n3 = self.store.record_serialized_snapshots(snaps2, collected_at=t0 + 20.0, force_heartbeat_seconds=60.0)
        self.assertEqual(n3, 1)

        # Same values, but heartbeat passed (>= 60s): should insert heartbeat row
        n4 = self.store.record_serialized_snapshots(snaps2, collected_at=t0 + 85.0, force_heartbeat_seconds=60.0)
        self.assertEqual(n4, 1)

    def test_query_history_burn_rate_and_eta(self):
        now = 10000.0
        # Simulate consumption:
        # At t = 6400 (1 hour before now): remaining = 90%
        # At t = 8200 (30 mins before now): remaining = 80%
        # At t = 10000 (now): remaining = 70%
        # Total consumption = 20% over 3600 seconds => 20% / hour!
        # Current pct = 70% => ETA = 70 / 20 = 3.5 hours = 12600 seconds
        snaps = [
            (
                now - 3600,
                {"agy": {"status": "ok", "metrics": {"gemini_5h": {"remaining_pct": 90, "detail": "90%"}}}},
            ),
            (
                now - 1800,
                {"agy": {"status": "ok", "metrics": {"gemini_5h": {"remaining_pct": 80, "detail": "80%"}}}},
            ),
            (
                now,
                {"agy": {"status": "ok", "metrics": {"gemini_5h": {"remaining_pct": 70, "detail": "70%"}}}},
            ),
        ]
        for t, s in snaps:
            self.store.record_serialized_snapshots(s, collected_at=t, force_heartbeat_seconds=0.0)

        res = self.store.query_history("agy", "gemini_5h", range_seconds=7200.0, max_points=50, now=now)
        self.assertEqual(res["provider"], "agy")
        self.assertEqual(res["metric"], "gemini_5h")
        self.assertEqual(len(res["points"]), 3)
        self.assertEqual(res["current_pct"], 70)
        self.assertAlmostEqual(res["burn_rate_pct_hr"], 20.0, delta=0.5)
        self.assertIsNotNone(res["eta_seconds"])
        self.assertAlmostEqual(res["eta_seconds"], 12600, delta=200)

    def test_query_history_downsampling(self):
        now = 50000.0
        # Insert 150 points
        for i in range(150):
            t = now - (150 - i) * 60
            s = {
                "claude1": {
                    "status": "ok",
                    "metrics": {"five_hour": {"remaining_pct": 100 - (i % 20), "detail": "active"}},
                }
            }
            self.store.record_serialized_snapshots(s, collected_at=t, force_heartbeat_seconds=0.0)

        # Query with max_points = 50
        res = self.store.query_history("claude1", "five_hour", range_seconds=86400.0, max_points=50, now=now)
        self.assertEqual(res["total_points"], 150)
        self.assertEqual(len(res["points"]), 50)
        # First point and last point should match extremes
        self.assertAlmostEqual(res["points"][0]["t"], now - 150 * 60, delta=1.0)
        self.assertAlmostEqual(res["points"][-1]["t"], now - 60, delta=1.0)

    def test_get_available_metrics(self):
        snaps = {
            "agy": {"status": "ok", "metrics": {"gemini_5h": {"remaining_pct": 95}}},
            "codex": {"status": "ok", "metrics": {"five_hour": {"remaining_pct": 50}}},
        }
        self.store.record_serialized_snapshots(snaps, collected_at=1000.0)
        metrics = self.store.get_available_metrics()
        pairs = [(m["provider"], m["metric"]) for m in metrics]
        self.assertIn(("agy", "gemini_5h"), pairs)
        self.assertIn(("codex", "five_hour"), pairs)

    def test_telemetry_service_records_to_history(self):
        mock_provider = MagicMock()
        mock_provider.key = "agy"
        mock_provider.collect.return_value = ProviderSnapshot(
            provider="agy",
            status="ok",
            metrics={"gemini_5h": Metric(remaining_pct=88, reset_at=None, detail="88/100")},
        )
        mock_profile = MagicMock()
        mock_profile.get_active_email.return_value = ""

        telemetry = TelemetryService(
            providers=[mock_provider],
            profile_service=mock_profile,
            history_store=self.store,
            refresh_interval=1.0,
        )
        telemetry.collect()

        recent = self.store.recent("agy", limit=10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0][1], "agy")
        self.assertEqual(recent[0][2], "gemini_5h")
        self.assertEqual(recent[0][4], 88)


if __name__ == "__main__":
    unittest.main()
