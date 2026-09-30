"""Safe local switching between Claude profile files."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def switch_profile(active_path: str | os.PathLike[str], source_path: str | os.PathLike[str], backup_dir: str | os.PathLike[str]) -> Path | None:
    active = Path(active_path)
    source = Path(source_path)
    backup: Path | None = None
    if not source.exists():
        raise FileNotFoundError(source)

    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    if active.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = Path(backup_dir) / f"claude-active-{stamp}.json"
        shutil.copy2(active, backup)

    active.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="claude-active.", suffix=".tmp", dir=active.parent)
    os.close(fd)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, active)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return backup
