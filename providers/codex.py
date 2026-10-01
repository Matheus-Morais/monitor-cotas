"""OpenAI Codex CLI telemetry provider."""

from __future__ import annotations

import os
from pathlib import Path

from providers.base import BaseProvider
from quota_core import ProviderSnapshot, load_codex_snapshot


class CodexProvider(BaseProvider):
    """Telemetry provider for OpenAI Codex CLI sessions and rolling usage."""

    def __init__(
        self,
        history_db: Path | str | None = None,
        state_db: Path | str | None = None,
        rollouts_dir: Path | str | None = None,
        key: str = "codex",
        display_name: str = "OpenAI Codex",
    ):
        super().__init__(key=key, display_name=display_name)
        home = Path.home()
        codex_dir = home / ".codex"

        self.history_db = Path(
            history_db or os.environ.get("CODEX_HISTORY_DB") or (codex_dir / "thread_history_1.sqlite")
        )
        self.state_db = Path(
            state_db or os.environ.get("CODEX_STATE_DB") or (codex_dir / "state_5.sqlite")
        )
        self.rollouts_dir = Path(
            rollouts_dir or os.environ.get("CODEX_ROLLOUTS_DIR") or (codex_dir / "sessions")
        )

    def is_available(self) -> bool:
        return self.history_db.exists() or self.state_db.exists() or self.rollouts_dir.exists()

    def collect(self) -> ProviderSnapshot:
        try:
            return load_codex_snapshot(
                str(self.history_db),
                str(self.state_db),
                rollouts_dir=str(self.rollouts_dir),
            )
        except Exception as exc:
            return ProviderSnapshot(
                provider="codex",
                status="unavailable",
                metrics={},
                metadata={"error": str(exc)},
            )
