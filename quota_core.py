"""Shared data collection and normalization for the quota monitor surfaces."""

from __future__ import annotations

import datetime as _datetime
import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


OK = "ok"
UNAVAILABLE = "unavailable"
ERROR = "error"


@dataclass(frozen=True)
class Metric:
    """A normalized quota value. ``remaining_pct=None`` means unknown."""

    remaining_pct: int | None = None
    reset_at: Any = None
    detail: str = ""
    estimated: bool = False


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: str
    status: str
    metrics: dict[str, Metric] = field(default_factory=dict)
    model: str = ""
    plan: str = ""
    account: str = ""
    source_age_seconds: int | None = None
    error: str = ""
    estimated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def format_countdown(epoch_or_iso: Any, *, now: float | None = None) -> str:
    """Return a compact countdown for epoch seconds or an ISO timestamp."""
    if not epoch_or_iso:
        return "Pronto"

    epoch: float | None = None
    if isinstance(epoch_or_iso, (int, float)):
        epoch = float(epoch_or_iso)
    elif isinstance(epoch_or_iso, str):
        try:
            epoch = _datetime.datetime.fromisoformat(
                epoch_or_iso.replace("Z", "+00:00")
            ).timestamp()
        except (TypeError, ValueError, OverflowError):
            return epoch_or_iso

    if epoch is None:
        return "Pronto"

    diff = int(epoch - (time.time() if now is None else now))
    if diff <= 0:
        return "Resetado"

    hours, remainder = divmod(diff, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _age_seconds(path: str | os.PathLike[str]) -> int | None:
    try:
        return max(0, int(time.time() - os.path.getmtime(path)))
    except (FileNotFoundError, OSError):
        return None


def _read_json(path: str | os.PathLike[str]) -> tuple[dict[str, Any] | None, str, int | None]:
    path = os.fspath(path)
    if not os.path.exists(path):
        return None, UNAVAILABLE, None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return None, ERROR, _age_seconds(path)
        return data, OK, _age_seconds(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, ERROR, _age_seconds(path)


def _percent(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 100:
        return None
    return round(number)


def _remaining_from_fraction(quota: Any) -> Metric:
    if not isinstance(quota, dict):
        return Metric()
    fraction = quota.get("remaining_fraction")
    if isinstance(fraction, bool):
        return Metric(reset_at=quota.get("reset_time"))
    try:
        remaining = round(float(fraction) * 100)
    except (TypeError, ValueError):
        remaining = None
    if remaining is not None and not 0 <= remaining <= 100:
        remaining = None
    return Metric(remaining, quota.get("reset_time"))


def _remaining_from_used(quota: Any, field: str) -> Metric:
    if not isinstance(quota, dict):
        return Metric()
    used = _percent(quota.get(field))
    remaining = None if used is None else 100 - used
    return Metric(remaining, quota.get("resets_at"))


def _snapshot_status(metrics: dict[str, Metric], source_status: str) -> str:
    if source_status == ERROR:
        return ERROR
    return OK if any(metric.remaining_pct is not None for metric in metrics.values()) else UNAVAILABLE


def load_agy_snapshot(path: str | os.PathLike[str]) -> ProviderSnapshot:
    data, source_status, age = _read_json(path)
    if data is None:
        return ProviderSnapshot("antigravity", source_status, source_age_seconds=age)

    quotas = data.get("quota")
    if not isinstance(quotas, dict):
        return ProviderSnapshot("antigravity", ERROR, source_age_seconds=age, error="quota ausente")

    metrics = {
        "gemini_5h": _remaining_from_fraction(quotas.get("gemini-5h")),
        "gemini_weekly": _remaining_from_fraction(quotas.get("gemini-weekly")),
        "3p_5h": _remaining_from_fraction(quotas.get("3p-5h")),
        "3p_weekly": _remaining_from_fraction(quotas.get("3p-weekly")),
    }
    model = data.get("model", {})
    model_name = model.get("display_name", "") if isinstance(model, dict) else ""
    return ProviderSnapshot(
        "antigravity",
        _snapshot_status(metrics, source_status),
        metrics,
        model=str(model_name or ""),
        plan=str(data.get("plan_tier", "") or ""),
        source_age_seconds=age,
    )


def load_claude_snapshot(path: str | os.PathLike[str]) -> ProviderSnapshot:
    data, source_status, age = _read_json(path)
    if data is None:
        return ProviderSnapshot("claude", source_status, source_age_seconds=age)

    cached = data.get("cachedUsageUtilization", {})
    utilization = cached.get("utilization") if isinstance(cached, dict) else None
    rate_limits = data.get("rate_limits")
    metrics: dict[str, Metric]

    if isinstance(utilization, dict):
        metrics = {
            "five_hour": _remaining_from_used(utilization.get("five_hour"), "utilization"),
            "seven_day": _remaining_from_used(utilization.get("seven_day"), "utilization"),
        }
        context = data.get("context_window")
        context_pct = context.get("remaining_percentage") if isinstance(context, dict) else None
        metrics["context"] = Metric(_percent(context_pct))
    elif isinstance(rate_limits, dict):
        metrics = {
            "five_hour": _remaining_from_used(rate_limits.get("five_hour"), "used_percentage"),
            "seven_day": _remaining_from_used(rate_limits.get("seven_day"), "used_percentage"),
        }
        context = data.get("context_window")
        context_pct = context.get("remaining_percentage") if isinstance(context, dict) else None
        metrics["context"] = Metric(_percent(context_pct))
    else:
        return ProviderSnapshot("claude", ERROR, source_age_seconds=age, error="limites ausentes")

    model = data.get("model", {})
    model_name = model.get("display_name", "") if isinstance(model, dict) else ""
    account = data.get("oauthAccount", {})
    email = account.get("emailAddress", "") if isinstance(account, dict) else ""
    return ProviderSnapshot(
        "claude",
        _snapshot_status(metrics, source_status),
        metrics,
        model=str(model_name or ""),
        account=str(email or ""),
        source_age_seconds=age,
        metadata={"has_oauth_account": isinstance(account, dict) and bool(account)},
    )


def _query_codex_databases(
    history_db: str | os.PathLike[str], state_db: str | os.PathLike[str], now: float
) -> dict[str, Any]:
    five_hour_ago = now - 5 * 3600
    seven_day_ago = now - 7 * 86400
    result: dict[str, Any] = {
        "available": False,
        "turns_5h": None,
        "first_turn_5h": None,
        "last_turn": None,
        "turns_7d": None,
        "tokens_5h": None,
        "tokens_7d": None,
    }

    if os.path.exists(history_db):
        try:
            with closing(sqlite3.connect(f"file:{Path(history_db)}?mode=ro", uri=True)) as conn:
                cur = conn.cursor()
                row = cur.execute(
                    "SELECT count(*), min(started_at), max(started_at) "
                    "FROM thread_turns WHERE started_at > ?;",
                    (five_hour_ago,),
                ).fetchone()
                result["turns_5h"], result["first_turn_5h"], result["last_turn"] = row
                result["turns_7d"] = cur.execute(
                    "SELECT count(*) FROM thread_turns WHERE started_at > ?;",
                    (seven_day_ago,),
                ).fetchone()[0]
            result["available"] = True
        except (OSError, sqlite3.Error):
            pass

    if os.path.exists(state_db):
        try:
            with closing(sqlite3.connect(f"file:{Path(state_db)}?mode=ro", uri=True)) as conn:
                cur = conn.cursor()
                result["tokens_5h"] = cur.execute(
                    "SELECT coalesce(sum(tokens_used), 0) FROM threads WHERE updated_at_ms > ?;",
                    (int(five_hour_ago * 1000),),
                ).fetchone()[0]
                result["tokens_7d"] = cur.execute(
                    "SELECT coalesce(sum(tokens_used), 0) FROM threads WHERE updated_at_ms > ?;",
                    (int(seven_day_ago * 1000),),
                ).fetchone()[0]
            result["available"] = True
        except (OSError, sqlite3.Error):
            pass

    return result


def load_codex_snapshot(
    history_db: str | os.PathLike[str],
    state_db: str | os.PathLike[str],
    model: str = "gpt-5.6-luna",
    limit_5h: int = 50,
    now: float | None = None,
) -> ProviderSnapshot:
    now = time.time() if now is None else now
    stats = _query_codex_databases(history_db, state_db, now)
    if not stats["available"]:
        return ProviderSnapshot("codex", UNAVAILABLE, model=model)

    metrics: dict[str, Metric] = {}
    if stats["turns_5h"] is not None:
        remaining = max(0, round((1 - stats["turns_5h"] / limit_5h) * 100))
        reset = stats["first_turn_5h"] + 5 * 3600 if stats["first_turn_5h"] else None
        metrics["five_hour"] = Metric(
            remaining, reset, f"{stats['turns_5h']}/{limit_5h} turnos", estimated=True
        )
    if stats["tokens_5h"] is not None:
        metrics["tokens_5h"] = Metric(
            min(100, int((stats["tokens_5h"] / 1_000_000) * 2)),
            detail=f"{round(stats['tokens_5h'] / 1_000_000, 1)}M tokens",
            estimated=True,
        )
    if stats["turns_7d"] is not None:
        metrics["seven_day"] = Metric(
            min(100, stats["turns_7d"] * 2),
            detail=f"{stats['turns_7d']} turnos",
            estimated=True,
        )

    last_active = ""
    if stats["last_turn"]:
        elapsed = max(0, int(now - stats["last_turn"]))
        if elapsed < 60:
            last_active = f"ha {elapsed}s"
        elif elapsed < 3600:
            last_active = f"ha {elapsed // 60}m"
        else:
            last_active = f"ha {elapsed // 3600}h"

    return ProviderSnapshot(
        "codex",
        _snapshot_status(metrics, OK),
        metrics,
        model=model,
        estimated=True,
        metadata={"last_active": last_active, "limit_5h": limit_5h},
    )
