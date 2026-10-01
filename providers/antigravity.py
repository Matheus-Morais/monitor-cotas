"""Google Antigravity telemetry provider."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from providers.base import BaseProvider
from quota_core import ProviderSnapshot, load_agy_snapshot

DEFAULT_AGY_STATUS = Path.home() / "scripts" / "agy-statusline-input.json"


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

    def is_available(self) -> bool:
        return self.status_path.exists()

    def collect(self) -> ProviderSnapshot:
        try:
            return load_agy_snapshot(str(self.status_path))
        except Exception as exc:
            return ProviderSnapshot(
                provider="antigravity",
                status="unavailable",
                metrics={},
                metadata={"error": str(exc)},
            )

    def trigger_refresh(self) -> bool:
        """Execute `agy --print /usage` asynchronously to refresh CLI cache."""
        def _run():
            try:
                subprocess.run(
                    ["agy", "--print", "/usage"],
                    capture_output=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True, name="agy-refresh-poller").start()
        return True
