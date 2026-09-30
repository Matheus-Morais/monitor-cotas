"""User configuration for the quota monitor.

Only paths and UI preferences live here. Authentication files and provider data
remain in their original locations and are never copied into the project.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


def runtime_home() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE") or Path.home())
    return Path(os.environ.get("HOME") or Path.home())


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "MonitorCotas"
    return runtime_home() / ".config" / "MonitorCotas"


def default_config_path() -> Path:
    return app_data_dir() / "config.json"


@dataclass
class MonitorConfig:
    agy_status_json: Path
    claude_status_json: Path
    claude_active_json: Path
    claude_profile_1_json: Path
    claude_profile_2_json: Path
    codex_config: Path
    codex_state_db: Path
    codex_history_db: Path
    codex_rollouts_dir: Path
    history_db: Path
    refresh_seconds: float = 5.0
    agy_poll_seconds: float = 120.0
    alert_threshold_pct: int = 15
    alert_recovery_pct: int = 80
    alert_cooldown_seconds: float = 3600.0
    history_retention_days: int = 30
    topmost: bool = True
    opacity: float = 0.90
    window_width: int = 520
    window_height: int = 600
    window_x: int | None = None
    window_y: int | None = None
    codex_limit_5h: int = 50
    codex_tokens_scale: float = 2.0
    global_hotkey: str = "ctrl+shift+c"

    @classmethod
    def defaults(cls) -> "MonitorConfig":
        home = runtime_home()
        codex = home / ".codex"
        return cls(
            agy_status_json=home / "scripts" / "agy-statusline-input.json",
            claude_status_json=home / "scripts" / "claude-statusline-input.json",
            claude_active_json=home / ".claude.json",
            claude_profile_1_json=home / ".claude-1.json",
            claude_profile_2_json=home / ".claude-2.json",
            codex_config=codex / "config.toml",
            codex_state_db=codex / "state_5.sqlite",
            codex_history_db=codex / "thread_history_1.sqlite",
            codex_rollouts_dir=codex / "sessions",
            history_db=app_data_dir() / "history.sqlite",
        )

    def normalized(self) -> "MonitorConfig":
        self.refresh_seconds = max(1.0, float(self.refresh_seconds))
        self.agy_poll_seconds = max(30.0, float(self.agy_poll_seconds))
        self.alert_threshold_pct = min(99, max(0, int(self.alert_threshold_pct)))
        self.alert_recovery_pct = min(100, max(self.alert_threshold_pct + 1, int(self.alert_recovery_pct)))
        self.alert_cooldown_seconds = max(60.0, float(self.alert_cooldown_seconds))
        self.history_retention_days = min(3650, max(1, int(self.history_retention_days)))
        self.opacity = min(1.0, max(0.35, float(self.opacity)))
        self.window_width = max(360, int(self.window_width))
        self.window_height = max(300, int(self.window_height))
        self.codex_limit_5h = max(1, int(self.codex_limit_5h))
        self.codex_tokens_scale = max(0.1, float(self.codex_tokens_scale))
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {
            key: str(value) if isinstance(value, Path) else value
            for key, value in data.items()
        }


def _from_dict(values: dict[str, Any]) -> MonitorConfig:
    defaults = MonitorConfig.defaults()
    known = {item.name for item in fields(MonitorConfig)}
    for key, value in values.items():
        if key not in known:
            continue
        if (
            key.endswith("_json")
            or key.endswith("_db")
            or key.endswith("_dir")
            or key == "codex_config"
            or key == "history_db"
        ):
            value = Path(value)
        setattr(defaults, key, value)
    return defaults.normalized()


def load_config(path: str | os.PathLike[str] | None = None) -> MonitorConfig:
    config_path = Path(path) if path else default_config_path()
    if not config_path.exists():
        return MonitorConfig.defaults().normalized()
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return _from_dict(raw if isinstance(raw, dict) else {})
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return MonitorConfig.defaults().normalized()


def save_config(config: MonitorConfig, path: str | os.PathLike[str] | None = None) -> Path:
    config_path = Path(path) if path else default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.normalized().to_dict(), ensure_ascii=False, indent=2)
    fd, temporary = tempfile.mkstemp(prefix="config.", suffix=".tmp", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        os.replace(temporary, config_path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return config_path
