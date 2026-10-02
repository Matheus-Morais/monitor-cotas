"""Google Antigravity telemetry provider."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import time
from providers.base import BaseProvider
from quota_core import Metric, ProviderSnapshot, load_agy_snapshot

DEFAULT_AGY_STATUS = Path.home() / "scripts" / "agy-statusline-input.json"


def parse_agy_usage_output(text: str) -> dict[str, Metric]:
    """Parse tabular quota output from `agy --print /usage`."""
    metrics: dict[str, Metric] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if len(parts) >= 3:
            model_group = parts[0].lower()
            limit_name = parts[1].lower()
            pct_str = parts[2].rstrip("%")
            reset_at = parts[3] if len(parts) >= 4 else None
            try:
                pct = int(pct_str)
            except ValueError:
                pct = None

            if "gemini" in model_group:
                if "five" in limit_name or "5h" in limit_name:
                    metrics["gemini_5h"] = Metric(pct, reset_at)
                elif "week" in limit_name or "7d" in limit_name:
                    metrics["gemini_weekly"] = Metric(pct, reset_at)
            elif "claude" in model_group or "gpt" in model_group or "3p" in model_group:
                if "five" in limit_name or "5h" in limit_name:
                    metrics["3p_5h"] = Metric(pct, reset_at)
                elif "week" in limit_name or "7d" in limit_name:
                    metrics["3p_weekly"] = Metric(pct, reset_at)
    return metrics


class AntigravityProvider(BaseProvider):
    """Telemetry provider for Google Antigravity quotas."""

    def __init__(
        self,
        status_path: Path | str | None = None,
        key: str = "agy",
        display_name: str = "Google Antigravity",
    ):
        super().__init__(key=key, display_name=display_name)
        if status_path:
            self.status_path = Path(status_path)
        else:
            env_val = os.environ.get("AGY_STATUS_JSON")
            self.status_path = Path(env_val) if env_val else DEFAULT_AGY_STATUS

        self._cached_cli_metrics: dict[str, Metric] = {}
        self._cached_cli_time: float = 0.0
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return self.status_path.exists() or bool(self._cached_cli_metrics)

    def collect(self) -> ProviderSnapshot:
        snapshot: ProviderSnapshot | None = None
        if self.status_path.exists():
            try:
                snapshot = load_agy_snapshot(str(self.status_path))
            except Exception as exc:
                snapshot = ProviderSnapshot(
                    provider="antigravity",
                    status="unavailable",
                    metrics={},
                    metadata={"error": str(exc)},
                )

        has_metrics = bool(
            snapshot
            and snapshot.metrics
            and any(m.remaining_pct is not None for m in snapshot.metrics.values())
        )

        with self._lock:
            cli_metrics = dict(self._cached_cli_metrics)

        # If file snapshot lacks metrics, fallback to cached CLI metrics
        if not has_metrics and cli_metrics:
            source_age = max(0, int(time.time() - self._cached_cli_time)) if self._cached_cli_time else None
            return ProviderSnapshot(
                provider="antigravity",
                status="ok",
                metrics=cli_metrics,
                source_age_seconds=source_age,
                plan=snapshot.plan if snapshot and snapshot.plan else "Google AI Pro",
                model=snapshot.model if snapshot and snapshot.model else "",
            )

        if snapshot is not None:
            return snapshot

        return ProviderSnapshot(
            provider="antigravity",
            status="unavailable",
            metrics={},
            metadata={"error": "Status file not found"},
        )

    def trigger_refresh(self) -> bool:
        """Execute `agy --print /usage` asynchronously to refresh CLI cache and capture live usage."""
        def _run():
            try:
                proc = subprocess.run(
                    ["agy", "--print", "/usage"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if proc.returncode == 0 and proc.stdout:
                    parsed = parse_agy_usage_output(proc.stdout)
                    if parsed:
                        with self._lock:
                            self._cached_cli_metrics = parsed
                            self._cached_cli_time = time.time()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True, name="agy-refresh-poller").start()
        return True
