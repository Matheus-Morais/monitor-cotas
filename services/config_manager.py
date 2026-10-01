"""Unified and robust configuration manager for TokenWatch.

Supports hybrid portable and %APPDATA% resolution, schema validation,
and atomic file operations to prevent data corruption.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

DEFAULT_VISIBLE_METRICS: dict[str, list[str]] = {
    "agy": ["gemini_5h", "gemini_weekly", "3p_5h"],
    "claude1": ["five_hour", "seven_day"],
    "claude2": ["five_hour", "seven_day"],
    "codex": ["five_hour", "seven_day"],
}

DEFAULT_CONFIG: dict[str, Any] = {
    "ui_mode": "panel",
    "avatar_size": 72,
    "avatar_position": {"x": None, "y": None},
    "panel_geometry": {"x": None, "y": None, "width": 384, "height": 581},
    "show_in_taskbar": False,
    "compact_mode": False,
    "collapsed_cards": {
        "agy": False,
        "claude1": False,
        "claude2": False,
        "codex": False,
    },
    "visible_metrics": {k: list(v) for k, v in DEFAULT_VISIBLE_METRICS.items()},
    "refresh_seconds": 3.0,
    "agy_poll_seconds": 120.0,
}


def get_app_data_dir() -> Path:
    """Return the primary AppData directory for TokenWatch."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "TokenWatch"
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return home / ".config" / "TokenWatch"


def get_legacy_app_data_dir() -> Path:
    """Return legacy MonitorCotas AppData directory for migration."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "MonitorCotas"
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return home / ".config" / "MonitorCotas"


def get_portable_dir() -> Path:
    """Return the directory containing the running executable or script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def resolve_config_path(explicit_path: Path | str | None = None) -> Path:
    """Resolve the configuration file path using a hybrid strategy:
    1. Explicit path if provided.
    2. Portable config.json in app directory if it exists.
    3. User config in %APPDATA%/TokenWatch/config.json.
    4. Migrates from %APPDATA%/MonitorCotas/config.json if TokenWatch doesn't exist yet.
    """
    if explicit_path is not None:
        return Path(explicit_path)

    # 1. Check portable config
    portable = get_portable_dir() / "config.json"
    if portable.exists():
        return portable

    # 2. Check TokenWatch AppData
    app_data = get_app_data_dir()
    primary = app_data / "config.json"
    if primary.exists():
        return primary

    # 3. Check Legacy AppData migration
    legacy = get_legacy_app_data_dir() / "config.json"
    if legacy.exists():
        try:
            app_data.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, primary)
            return primary
        except OSError:
            return legacy

    # Default to AppData path (will be created on save)
    return primary


def resolve_history_db_path(explicit_path: Path | str | None = None) -> Path:
    """Resolve the SQLite history database path using hybrid portable / AppData strategy."""
    if explicit_path is not None:
        return Path(explicit_path)

    # 1. Portable history DB
    portable = get_portable_dir() / "history.sqlite"
    if portable.exists():
        return portable

    # 2. TokenWatch AppData
    app_data = get_app_data_dir()
    primary = app_data / "history.sqlite"
    if primary.exists():
        return primary

    # 3. Check legacy MonitorCotas history DB
    legacy = get_legacy_app_data_dir() / "history.sqlite"
    if legacy.exists():
        try:
            app_data.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, primary)
            return primary
        except OSError:
            return legacy

    return primary


def _int_or(value: Any, fallback: int | None, *, minimum: int | None = None) -> int | None:
    if isinstance(value, bool):
        return fallback
    try:
        result = int(value)
    except (TypeError, ValueError):
        return fallback
    if minimum is not None and result < minimum:
        return fallback
    return result


def _float_or(value: Any, fallback: float, *, minimum: float = 0.1) -> float:
    try:
        result = float(value)
        return max(minimum, result)
    except (TypeError, ValueError):
        return fallback


def normalize_config(raw: Any) -> dict[str, Any]:
    """Normalize input config, filling defaults and fixing legacy keys."""
    config: dict[str, Any] = {
        "ui_mode": DEFAULT_CONFIG["ui_mode"],
        "avatar_size": DEFAULT_CONFIG["avatar_size"],
        "avatar_position": dict(DEFAULT_CONFIG["avatar_position"]),
        "panel_geometry": dict(DEFAULT_CONFIG["panel_geometry"]),
        "show_in_taskbar": DEFAULT_CONFIG["show_in_taskbar"],
        "compact_mode": DEFAULT_CONFIG["compact_mode"],
        "collapsed_cards": dict(DEFAULT_CONFIG["collapsed_cards"]),
        "visible_metrics": {k: list(v) for k, v in DEFAULT_VISIBLE_METRICS.items()},
        "refresh_seconds": DEFAULT_CONFIG["refresh_seconds"],
        "agy_poll_seconds": DEFAULT_CONFIG["agy_poll_seconds"],
    }
    if not isinstance(raw, dict):
        return config

    mode = raw.get("ui_mode", raw.get("mode", config["ui_mode"]))
    config["ui_mode"] = mode if mode in {"avatar", "panel"} else "panel"

    config["avatar_size"] = _int_or(
        raw.get("avatar_size", raw.get("avatarSize")), config["avatar_size"], minimum=32
    ) or config["avatar_size"]

    # Position
    pos = raw.get("avatar_position", raw.get("position"))
    if isinstance(pos, dict):
        config["avatar_position"] = {
            "x": _int_or(pos.get("x"), None),
            "y": _int_or(pos.get("y"), None),
        }

    # Panel Geometry
    geom = raw.get("panel_geometry", raw.get("panel"))
    if isinstance(geom, dict):
        config["panel_geometry"] = {
            "x": _int_or(geom.get("x"), None),
            "y": _int_or(geom.get("y"), None),
            "width": _int_or(geom.get("width"), config["panel_geometry"]["width"], minimum=240),
            "height": _int_or(geom.get("height"), config["panel_geometry"]["height"], minimum=180),
        }
    if "width" in raw or "height" in raw:
        config["panel_geometry"]["width"] = _int_or(
            raw.get("width"), config["panel_geometry"]["width"], minimum=240
        )
        config["panel_geometry"]["height"] = _int_or(
            raw.get("height"), config["panel_geometry"]["height"], minimum=180
        )

    # Flags
    config["show_in_taskbar"] = bool(raw.get("show_in_taskbar", config["show_in_taskbar"]))
    config["compact_mode"] = bool(raw.get("compact_mode", config["compact_mode"]))

    # Collapsed Cards
    if isinstance(raw.get("collapsed_cards"), dict):
        config["collapsed_cards"] = {
            str(k): bool(v) for k, v in raw["collapsed_cards"].items()
        }

    # Visible metrics
    if isinstance(raw.get("visible_metrics"), dict):
        vm: dict[str, list[str]] = {}
        for prov, defs in DEFAULT_VISIBLE_METRICS.items():
            loaded = raw["visible_metrics"].get(prov)
            if isinstance(loaded, list):
                vm[prov] = [str(x) for x in loaded if str(x) in defs]
            else:
                vm[prov] = list(defs)
        config["visible_metrics"] = vm

    # Polling intervals
    config["refresh_seconds"] = _float_or(raw.get("refresh_seconds"), config["refresh_seconds"], minimum=1.0)
    config["agy_poll_seconds"] = _float_or(raw.get("agy_poll_seconds"), config["agy_poll_seconds"], minimum=10.0)

    return config


class ConfigManager:
    """Thread-safe manager for loading, normalizing, and saving configuration."""

    def __init__(self, config_path: Path | str | None = None):
        self.config_path = resolve_config_path(config_path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = self.load()

    @property
    def data(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.config_path.exists():
                self._data = normalize_config({})
                return dict(self._data)
            try:
                content = self.config_path.read_text(encoding="utf-8")
                raw = json.loads(content)
                self._data = normalize_config(raw)
            except Exception:
                self._data = normalize_config({})
            return dict(self._data)

    def save(self, new_data: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if new_data is not None:
                self._data = normalize_config(new_data)

            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self._data, indent=2) + "\n"

            # Atomic save via tempfile and os.replace
            fd, temp_path = tempfile.mkstemp(
                prefix="config-",
                suffix=".tmp",
                dir=str(self.config_path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                os.replace(temp_path, str(self.config_path))
            except Exception:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise

            return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self.save(self._data)

    def update(self, partial: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            merged = dict(self._data)
            merged.update(partial)
            return self.save(merged)
