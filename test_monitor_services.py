import json
import queue
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import CollectorWorker, DashboardSnapshot, QuotaCollector
from config import MonitorConfig, app_data_dir, load_config, save_config
from history import HistoryStore
from notifier import OptionalTray
from profiles import switch_profile
from quota_core import Metric, OK, ProviderSnapshot


def sample_snapshot(remaining=12, collected_at=1000):
    metric = Metric(remaining, reset_at=2000, detail="teste")
    provider = ProviderSnapshot("antigravity", OK, {"gemini_5h": metric})
    empty = ProviderSnapshot("claude", "unavailable")
    codex = ProviderSnapshot("codex", "unavailable")
    return DashboardSnapshot(collected_at, provider, empty, empty, empty, codex)


class DummyCollector:
    class Config:
        refresh_seconds = 0.01
        agy_poll_seconds = 3600

    def __init__(self):
        self.config = self.Config()
        self.count = 0

    def poll_agy(self, cancel_event=None):
        return True

    def collect(self):
        self.count += 1
        return sample_snapshot(collected_at=1000 + self.count)


class MonitorServicesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_config_uses_runtime_home(self):
        with patch.dict("os.environ", {"USERPROFILE": str(self.root), "APPDATA": str(self.root / "appdata")}, clear=False):
            config = MonitorConfig.defaults()
            self.assertEqual(config.agy_status_json, self.root / "scripts" / "agy-statusline-input.json")
            self.assertEqual(config.history_db, self.root / "appdata" / "MonitorCotas" / "history.sqlite")

    def test_presentations_use_config_paths(self):
        for name in ("quota_monitor.py", "quota_widget.py"):
            source = Path(name).read_text(encoding="utf-8")
            self.assertNotIn("C:\\Users\\MOBILTEC", source)
        config = MonitorConfig.defaults()
        self.assertTrue(config.claude_active_json.name.endswith(".json"))

    def test_gui_render_loop_has_no_provider_io(self):
        source = Path("quota_widget.py").read_text(encoding="utf-8")
        render_section = source.split("    def drain_results", 1)[1].split("\ndef main", 1)[0]
        for forbidden in ("load_agy_snapshot", "load_claude_snapshot", "load_codex_snapshot", "sqlite3", "subprocess.run", "open("):
            self.assertNotIn(forbidden, render_section)

    def test_refresh_uses_worker(self):
        source = Path("quota_widget.py").read_text(encoding="utf-8")
        refresh_section = source.split("    def force_update", 1)[1].split("    def switch_claude1", 1)[0]
        self.assertIn("self.worker.refresh_now()", refresh_section)
        self.assertNotIn("subprocess.run", refresh_section)

    def test_worker_collects_and_stops(self):
        results = queue.Queue()
        worker = CollectorWorker(DummyCollector(), results)
        worker.start()
        snapshot = results.get(timeout=2)
        self.assertEqual(snapshot.agy.status, OK)
        worker.stop(timeout=1)
        self.assertFalse(worker._thread.is_alive())

    def test_worker_refresh_event(self):
        results = queue.Queue()
        worker = CollectorWorker(DummyCollector(), results)
        worker.start()
        first = results.get(timeout=2)
        worker.refresh_now()
        second = results.get(timeout=2)
        worker.stop(timeout=1)
        self.assertGreater(second.collected_at, first.collected_at)

    def test_agy_poll_uses_popen_pipes(self):
        class CompletedProcess:
            returncode = 0

            def poll(self):
                return 0

        with patch("collector.subprocess.Popen", return_value=CompletedProcess()) as popen:
            self.assertTrue(QuotaCollector(MonitorConfig.defaults()).poll_agy())
        kwargs = popen.call_args.kwargs
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.PIPE)
        self.assertNotIn("capture_output", kwargs)

    def test_history_records_and_reads_snapshots(self):
        store = HistoryStore(self.root / "history.sqlite")
        count = store.record_dashboard(sample_snapshot())
        rows = store.recent()
        self.assertEqual(count, 1)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("email", json.dumps(rows).lower())
        self.assertNotIn("token", json.dumps(rows).lower())

    def test_dashboard_deduplicates_claude_accounts_by_identity(self):
        from collector import DashboardSnapshot

        active = ProviderSnapshot("claude", OK, account="same@example.com", metadata={"account_uuid": "same"})
        profile_1 = ProviderSnapshot("claude", OK, account="same@example.com", metadata={"account_uuid": "same"})
        profile_2 = ProviderSnapshot("claude", OK, account="other@example.com", metadata={"account_uuid": "other"})
        snapshot = DashboardSnapshot(1000, ProviderSnapshot("antigravity", "unavailable"), active, profile_1, profile_2, ProviderSnapshot("codex", "unavailable"))

        providers = snapshot.providers()

        self.assertNotIn("claude", providers)
        self.assertIn("claude_profile_1", providers)
        self.assertIn("claude_profile_2", providers)

    def test_alert_cooldown_and_recovery(self):
        store = HistoryStore(self.root / "history.sqlite")
        first = store.evaluate_alerts(sample_snapshot(12), 15, 80, 3600, now=1000)
        repeated = store.evaluate_alerts(sample_snapshot(10), 15, 80, 3600, now=1001)
        after_cooldown = store.evaluate_alerts(sample_snapshot(8), 15, 80, 3600, now=4601)
        recovered = store.evaluate_alerts(sample_snapshot(90), 15, 80, 3600, now=4602)
        self.assertEqual([event.kind for event in first], ["low"])
        self.assertEqual(repeated, [])
        self.assertEqual([event.kind for event in after_cooldown], ["low"])
        self.assertEqual([event.kind for event in recovered], ["recovered"])

    def test_profile_switch_creates_backup(self):
        active = self.root / "active.json"
        source = self.root / "profile.json"
        backup_dir = self.root / "backups"
        active.write_text('{"account":"old"}', encoding="utf-8")
        source.write_text('{"account":"new"}', encoding="utf-8")
        backup = switch_profile(active, source, backup_dir)
        self.assertIsNotNone(backup)
        self.assertEqual(json.loads(active.read_text(encoding="utf-8"))["account"], "new")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["account"], "old")

    def test_window_preferences_round_trip(self):
        path = self.root / "config.json"
        config = MonitorConfig.defaults()
        config.window_x, config.window_y, config.opacity, config.topmost = 12, 34, 0.75, False
        save_config(config, path)
        loaded = load_config(path)
        self.assertEqual((loaded.window_x, loaded.window_y), (12, 34))
        self.assertEqual(loaded.opacity, 0.75)
        self.assertFalse(loaded.topmost)

    def test_tray_is_optional(self):
        tray = OptionalTray(lambda: None, lambda: None)
        with patch("builtins.__import__", side_effect=ImportError("optional dependency")):
            self.assertFalse(tray.start())
        self.assertFalse(tray.available)

    def test_gui_has_single_instance_guard(self):
        source = Path("quota_widget.py").read_text(encoding="utf-8")
        self.assertIn("Local\\\\MonitorCotas", source)
        self.assertIn("ERROR_ALREADY_EXISTS", source)

    def test_distribution_files_exist(self):
        for name in ("README.md", "requirements.txt", "build.ps1", "cotas-gui.cmd"):
            self.assertTrue(Path(name).exists(), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
