"""Anthropic Claude Code telemetry provider."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from providers.base import BaseProvider
from quota_core import ProviderSnapshot, load_claude_snapshot

DEFAULT_CLAUDE_STATUS = Path.home() / "scripts" / "claude-statusline-input.json"
DEFAULT_ACTIVE_PATH = Path.home() / ".claude.json"


class ClaudeProvider(BaseProvider):
    """Telemetry provider for Claude Code profile accounts."""

    def __init__(
        self,
        account_num: int = 1,
        profile_path: Path | str | None = None,
        status_path: Path | str | None = None,
        active_path: Path | str | None = None,
        active_email_getter: Callable[[], str] | None = None,
        key: str | None = None,
        display_name: str | None = None,
    ):
        self.account_num = account_num
        provider_key = key or f"claude{account_num}"
        provider_name = display_name or f"Claude (Conta {account_num})"
        super().__init__(key=provider_key, display_name=provider_name)

        home = Path.home()
        self.profile_path = Path(profile_path) if profile_path else (home / f".claude-{account_num}.json")
        if status_path:
            self.status_path = Path(status_path)
        else:
            env_val = os.environ.get("CLAUDE_STATUS_JSON")
            self.status_path = Path(env_val) if env_val else DEFAULT_CLAUDE_STATUS

        self.active_path = Path(active_path) if active_path else DEFAULT_ACTIVE_PATH
        self.active_email_getter = active_email_getter

    def is_available(self) -> bool:
        return self.profile_path.exists() or self.status_path.exists() or self.active_path.exists()

    def collect(self) -> ProviderSnapshot:
        target_path = self.profile_path if self.profile_path.exists() else self.status_path
        if not target_path.exists() and self.active_path.exists():
            target_path = self.active_path

        try:
            snapshot = load_claude_snapshot(str(target_path))
            active_email = self.active_email_getter() if self.active_email_getter else ""

            # If this account matches active email, read active_path for freshest token data
            if active_email and snapshot.account == active_email and self.active_path.exists():
                snapshot = load_claude_snapshot(str(self.active_path))

            return snapshot
        except Exception as exc:
            return ProviderSnapshot(
                provider="claude",
                status="unavailable",
                metrics={},
                metadata={"error": str(exc)},
            )
