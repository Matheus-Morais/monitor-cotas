"""Profile management service for switching Claude accounts safely.

Includes atomic file replacement, automatic rotating backups (keeping last N),
and non-blocking extraction of active account identity.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from services.config_manager import get_app_data_dir

DEFAULT_MAX_BACKUPS = 5


def get_default_backup_dir() -> Path:
    return get_app_data_dir() / "backups"


class ProfileService:
    """Manages Claude profile switching with atomic writes and backup retention."""

    def __init__(
        self,
        home_dir: Path | str | None = None,
        backup_dir: Path | str | None = None,
        max_backups: int = DEFAULT_MAX_BACKUPS,
    ):
        self.home_dir = Path(home_dir) if home_dir else Path.home()
        self.backup_dir = Path(backup_dir) if backup_dir else get_default_backup_dir()
        self.max_backups = max(1, max_backups)
        self.active_path = self.home_dir / ".claude.json"

    def get_active_email(self) -> str:
        """Read and return active Claude account email without throwing exceptions."""
        if not self.active_path.exists():
            return ""
        try:
            content = self.active_path.read_text(encoding="utf-8")
            data = json.loads(content)
            return data.get("oauthAccount", {}).get("emailAddress", "")
        except Exception:
            return ""

    def rotate_backups(self) -> list[Path]:
        """Prune backup directory, keeping only the most recent N backups."""
        if not self.backup_dir.exists():
            return []

        backups = sorted(
            [p for p in self.backup_dir.glob("claude-active-*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        excess = backups[self.max_backups:]
        for old in excess:
            try:
                old.unlink()
            except OSError:
                pass

        return backups[:self.max_backups]

    def switch_profile(
        self,
        source_path: Path | str,
        active_path: Path | str | None = None,
    ) -> Path | None:
        """Atomically switch active Claude profile to source_path.

        Creates a backup of current active profile and rotates old backups.
        Returns the path to the created backup if one was made.
        """
        source = Path(source_path)
        dest = Path(active_path) if active_path else self.active_path

        if not source.exists():
            raise FileNotFoundError(f"Source profile not found: {source}")

        backup_file: Path | None = None
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup_file = self.backup_dir / f"claude-active-{stamp}.json"
            shutil.copy2(dest, backup_file)
            self.rotate_backups()

        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_file = tempfile.mkstemp(
            prefix="claude-active-",
            suffix=".tmp",
            dir=str(dest.parent),
        )
        os.close(fd)

        try:
            shutil.copy2(source, temp_file)
            os.replace(temp_file, dest)
        except Exception:
            try:
                os.unlink(temp_file)
            except OSError:
                pass
            raise

        return backup_file

    def switch_account_number(self, account_num: int) -> bool:
        """Switch to ~/.claude-{account_num}.json."""
        source = self.home_dir / f".claude-{account_num}.json"
        try:
            self.switch_profile(source)
            return True
        except (OSError, FileNotFoundError):
            return False
