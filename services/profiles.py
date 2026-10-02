"""Profile management service for switching Claude accounts safely.

Includes atomic file replacement, automatic rotating backups (keeping last N),
bidirectional state synchronization between active .claude.json and profile files,
and preservation/restoration of per-profile credentials.
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
        self.claude_dir = self.home_dir / ".claude"
        self.credentials_path = self.claude_dir / ".credentials.json"

    def get_active_email(self) -> str:
        """Read and return active Claude account email without throwing exceptions."""
        return self.get_active_account_identity().get("email", "")

    def get_active_account_identity(self) -> dict[str, str]:
        """Read and return active Claude account email and account UUID."""
        if not self.active_path.exists():
            return {}
        try:
            content = self.active_path.read_text(encoding="utf-8")
            data = json.loads(content)
            oauth = data.get("oauthAccount", {})
            return {
                "email": str(oauth.get("emailAddress", "") or ""),
                "account_uuid": str(oauth.get("accountUuid", "") or ""),
            }
        except Exception:
            return {}

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

    def _sync_current_profile_before_switch(self, target_num: int) -> int | None:
        """Save active state and credentials back to the matching profile before switching."""
        if not self.active_path.exists():
            return None

        ident = self.get_active_account_identity()
        current_email = ident.get("email", "")
        current_uuid = ident.get("account_uuid", "")

        matched_num: int | None = None
        for n in range(1, 10):
            p = self.home_dir / f".claude-{n}.json"
            if not p.exists():
                continue
            try:
                p_data = json.loads(p.read_text(encoding="utf-8"))
                p_oauth = p_data.get("oauthAccount", {})
                p_email = str(p_oauth.get("emailAddress", "") or "")
                p_uuid = str(p_oauth.get("accountUuid", "") or "")
                if (current_uuid and p_uuid == current_uuid) or (current_email and p_email == current_email):
                    matched_num = n
                    break
            except Exception:
                continue

        # If not uniquely matched, infer from target (e.g. switching to 1 implies leaving 2)
        if matched_num is None:
            inferred = 2 if target_num == 1 else 1
            if (self.home_dir / f".claude-{inferred}.json").exists():
                matched_num = inferred

        if matched_num is not None:
            dest_profile = self.home_dir / f".claude-{matched_num}.json"
            try:
                shutil.copy2(self.active_path, dest_profile)
            except Exception:
                pass

            if self.credentials_path.exists():
                dest_cred = self.claude_dir / f".credentials-{matched_num}.json"
                try:
                    self.claude_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self.credentials_path, dest_cred)
                except Exception:
                    pass

        return matched_num

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
        """Switch to ~/.claude-{account_num}.json with full state & credential sync."""
        source = self.home_dir / f".claude-{account_num}.json"
        if not source.exists():
            return False

        try:
            # 1. Sync current active account back to its profile
            self._sync_current_profile_before_switch(account_num)

            # 2. Switch main .claude.json
            self.switch_profile(source)

            # 3. Restore matching credentials if saved
            target_cred = self.claude_dir / f".credentials-{account_num}.json"
            if target_cred.exists():
                try:
                    self.claude_dir.mkdir(parents=True, exist_ok=True)
                    fd, temp_cred = tempfile.mkstemp(
                        prefix="claude-cred-",
                        suffix=".tmp",
                        dir=str(self.claude_dir),
                    )
                    os.close(fd)
                    shutil.copy2(target_cred, temp_cred)
                    os.replace(temp_cred, self.credentials_path)
                except Exception:
                    pass

            return True
        except (OSError, FileNotFoundError):
            return False
