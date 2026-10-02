"""Provider collection and a background worker safe for Tkinter callers."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from config import MonitorConfig
from providers.claude import ClaudeProvider
from quota_core import ERROR, ProviderSnapshot, load_agy_snapshot, load_claude_snapshot, load_codex_snapshot


def read_codex_model(path: str | os.PathLike[str]) -> str:
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("model ="):
                return line.split("=", 1)[1].strip().strip('"') or "gpt-5.6-luna"
    except (OSError, UnicodeError):
        pass
    return "gpt-5.6-luna"


@dataclass(frozen=True)
class DashboardSnapshot:
    collected_at: float
    agy: ProviderSnapshot
    claude: ProviderSnapshot
    claude_profile_1: ProviderSnapshot
    claude_profile_2: ProviderSnapshot
    codex: ProviderSnapshot
    collector_error: str = ""

    def providers(self) -> dict[str, ProviderSnapshot]:
        providers = {"antigravity": self.agy}
        seen_claude: set[str] = set()
        for name, snapshot in (
            ("claude_profile_1", self.claude_profile_1),
            ("claude_profile_2", self.claude_profile_2),
            ("claude", self.claude),
        ):
            identity = str(snapshot.metadata.get("account_uuid") or snapshot.account or name)
            if identity in seen_claude:
                continue
            seen_claude.add(identity)
            providers[name] = snapshot
        providers["codex"] = self.codex
        return providers


class QuotaCollector:
    def __init__(self, config: MonitorConfig):
        self.config = config.normalized()

    def poll_agy(self, cancel_event: threading.Event | None = None) -> bool:
        process = None
        try:
            process = subprocess.Popen(
                ["agy", "--print", "/usage"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + 30
            while process.poll() is None and time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    process.wait(timeout=2)
                    return False
                time.sleep(0.1)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
                return False
            return process.returncode == 0
        except (OSError, subprocess.SubprocessError):
            if process is not None and process.poll() is None:
                process.kill()
            return False

    def collect(self) -> DashboardSnapshot:
        p1_path = self.config.claude_profile_1_json if self.config.claude_profile_1_json.exists() else self.config.claude_active_json
        p2_path = self.config.claude_profile_2_json if self.config.claude_profile_2_json.exists() else self.config.claude_active_json
        status_path = self.config.claude_status_json if hasattr(self.config, "claude_status_json") else None

        prov1 = ClaudeProvider(1, profile_path=p1_path, active_path=self.config.claude_active_json, status_path=status_path)
        prov2 = ClaudeProvider(2, profile_path=p2_path, active_path=self.config.claude_active_json, status_path=status_path)

        profile_1 = prov1.collect()
        profile_2 = prov2.collect()
        active = profile_1 if getattr(profile_1, "is_active_account", False) else (
            profile_2 if getattr(profile_2, "is_active_account", False) else profile_1
        )

        return DashboardSnapshot(
            collected_at=time.time(),
            agy=load_agy_snapshot(self.config.agy_status_json),
            claude=active,
            claude_profile_1=profile_1,
            claude_profile_2=profile_2,
            codex=load_codex_snapshot(
                self.config.codex_history_db,
                self.config.codex_state_db,
                model=read_codex_model(self.config.codex_config),
                limit_5h=self.config.codex_limit_5h,
                tokens_scale=self.config.codex_tokens_scale,
                rollouts_dir=self.config.codex_rollouts_dir,
            ),
        )


class CollectorWorker:
    """Collect snapshots away from the UI thread and publish them to a queue."""

    def __init__(self, collector: QuotaCollector, results: queue.Queue[DashboardSnapshot] | None = None):
        self.collector = collector
        self.results = results or queue.Queue()
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="quota-collector", daemon=True)
        self._thread.start()

    def refresh_now(self) -> None:
        self._refresh.set()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._refresh.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        # Paint local sources immediately.  The optional agy subprocess can
        # take several seconds on a cold start and must not blank the HUD.
        last_agy_poll = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_agy_poll >= self.collector.config.agy_poll_seconds:
                try:
                    self.collector.poll_agy(self._stop)
                except Exception:
                    # A slow or incompatible optional provider must not stop
                    # snapshots from the other providers reaching the UI.
                    pass
                last_agy_poll = now
            try:
                snapshot = self.collector.collect()
            except Exception as exc:  # keep the worker alive if a provider changes format
                snapshot = DashboardSnapshot(
                    collected_at=time.time(),
                    agy=ProviderSnapshot("antigravity", ERROR, error=str(exc)),
                    claude=ProviderSnapshot("claude", ERROR, error=str(exc)),
                    claude_profile_1=ProviderSnapshot("claude_profile_1", ERROR, error=str(exc)),
                    claude_profile_2=ProviderSnapshot("claude_profile_2", ERROR, error=str(exc)),
                    codex=ProviderSnapshot("codex", ERROR, error=str(exc)),
                    collector_error=str(exc),
                )
            self.results.put(snapshot)
            self._refresh.wait(timeout=self.collector.config.refresh_seconds)
            self._refresh.clear()
