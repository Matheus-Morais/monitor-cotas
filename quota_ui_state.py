"""Persistent, backwards-compatible UI preferences for the quota monitor."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_UI_CONFIG: dict[str, Any] = {
    "ui_mode": "avatar",
    "avatar_size": 72,
    "avatar_position": {"x": None, "y": None},
    "panel_geometry": {"x": None, "y": None, "width": 520, "height": 600},
    "show_in_taskbar": False,
    "compact_mode": False,
    "collapsed_cards": {},
}


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


def _position(raw: Any, fallback: dict[str, int | None]) -> dict[str, int | None]:
    if not isinstance(raw, dict):
        return dict(fallback)
    return {
        "x": _int_or(raw.get("x"), fallback["x"]),
        "y": _int_or(raw.get("y"), fallback["y"]),
    }


def _panel_geometry(raw: Any, fallback: dict[str, int | None]) -> dict[str, int | None]:
    if not isinstance(raw, dict):
        return dict(fallback)
    return {
        "x": _int_or(raw.get("x"), fallback["x"]),
        "y": _int_or(raw.get("y"), fallback["y"]),
        "width": _int_or(raw.get("width"), fallback["width"], minimum=360),
        "height": _int_or(raw.get("height"), fallback["height"], minimum=260),
    }


def default_ui_config() -> dict[str, Any]:
    """Return a fresh config so callers can safely mutate nested values."""
    return {
        "ui_mode": DEFAULT_UI_CONFIG["ui_mode"],
        "avatar_size": DEFAULT_UI_CONFIG["avatar_size"],
        "avatar_position": dict(DEFAULT_UI_CONFIG["avatar_position"]),
        "panel_geometry": dict(DEFAULT_UI_CONFIG["panel_geometry"]),
        "show_in_taskbar": DEFAULT_UI_CONFIG["show_in_taskbar"],
        "compact_mode": DEFAULT_UI_CONFIG["compact_mode"],
        "collapsed_cards": {},
    }


def normalize_ui_config(raw: Any) -> dict[str, Any]:
    """Normalize new settings and the small set of legacy aliases we have used."""
    config = default_ui_config()
    if not isinstance(raw, dict):
        return config

    mode = raw.get("ui_mode", raw.get("mode", config["ui_mode"]))
    config["ui_mode"] = mode if mode in {"avatar", "panel"} else "avatar"
    config["avatar_size"] = _int_or(
        raw.get("avatar_size", raw.get("avatarSize")), config["avatar_size"], minimum=48
    ) or config["avatar_size"]

    avatar_position = raw.get("avatar_position", raw.get("avatar_position_px", raw.get("position")))
    config["avatar_position"] = _position(avatar_position, config["avatar_position"])

    panel_geometry = raw.get("panel_geometry", raw.get("panel", raw.get("geometry")))
    config["panel_geometry"] = _panel_geometry(panel_geometry, config["panel_geometry"])
    if "width" in raw or "height" in raw:
        config["panel_geometry"]["width"] = _int_or(
            raw.get("width"), config["panel_geometry"]["width"], minimum=360
        )
        config["panel_geometry"]["height"] = _int_or(
            raw.get("height"), config["panel_geometry"]["height"], minimum=260
        )
    config["show_in_taskbar"] = bool(
        raw.get("show_in_taskbar", raw.get("taskbar", config["show_in_taskbar"]))
    )
    config["compact_mode"] = bool(raw.get("compact_mode", raw.get("compact", config["compact_mode"])))
    collapsed = raw.get("collapsed_cards", {})
    if isinstance(collapsed, dict):
        config["collapsed_cards"] = {key: bool(collapsed.get(key, False)) for key in ("agy", "claude1", "claude2", "codex")}
    return config


def load_ui_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    path = os.fspath(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raw = {}
    return normalize_ui_config(raw)


def save_ui_config(config: dict[str, Any], path: str | os.PathLike[str]) -> dict[str, Any]:
    """Persist normalized settings atomically and return what was written."""
    normalized = normalize_ui_config(config)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return normalized
