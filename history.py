"""Local snapshot history and durable alert state."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from collector import DashboardSnapshot
from quota_core import Metric, ProviderSnapshot


@dataclass(frozen=True)
class AlertEvent:
    provider: str
    metric: str
    remaining_pct: int
    kind: str
    message: str


class HistoryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_logged: dict[tuple[str, str], tuple[float, int | None, str]] = {}
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _initialize(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collected_at REAL NOT NULL,
                    provider TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    status TEXT NOT NULL,
                    remaining_pct INTEGER,
                    reset_at TEXT,
                    detail TEXT NOT NULL DEFAULT '',
                    estimated INTEGER NOT NULL DEFAULT 0,
                    source_age_seconds INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_provider_time
                    ON snapshots(provider, collected_at);
                CREATE INDEX IF NOT EXISTS idx_snapshots_prov_met_time
                    ON snapshots(provider, metric, collected_at);
                CREATE TABLE IF NOT EXISTS alert_state (
                    provider TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    last_notified_at REAL,
                    PRIMARY KEY(provider, metric)
                );
                """
            )
            conn.commit()

    @staticmethod
    def _reset_value(metric: Metric) -> str:
        if metric.reset_at is None:
            return ""
        return json.dumps(metric.reset_at, ensure_ascii=False)

    def record_dashboard(self, dashboard: DashboardSnapshot) -> int:
        rows = []
        for provider, snapshot in dashboard.providers().items():
            for metric_name, metric in snapshot.metrics.items():
                rows.append(
                    (
                        dashboard.collected_at,
                        provider,
                        metric_name,
                        snapshot.status,
                        metric.remaining_pct,
                        self._reset_value(metric),
                        metric.detail,
                        int(metric.estimated or snapshot.estimated),
                        snapshot.source_age_seconds,
                    )
                )
        if not rows:
            return 0
        with closing(self._connect()) as conn:
            conn.executemany(
                "INSERT INTO snapshots "
                "(collected_at, provider, metric, status, remaining_pct, reset_at, detail, estimated, source_age_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)

    def record_serialized_snapshots(
        self,
        snapshots: dict[str, Any],
        collected_at: float | None = None,
        force_heartbeat_seconds: float = 60.0,
    ) -> int:
        """Smart change-driven snapshot logging with periodic heartbeat."""
        now = time.time() if collected_at is None else collected_at
        rows = []

        for provider_key, snapshot in snapshots.items():
            if not isinstance(snapshot, dict):
                continue
            status = snapshot.get("status", "unavailable")
            source_age = snapshot.get("source_age_seconds")
            metrics = snapshot.get("metrics", {})

            for metric_key, metric_data in metrics.items():
                if not isinstance(metric_data, dict):
                    continue
                rem_pct = metric_data.get("remaining_pct")
                reset_at = metric_data.get("reset_at")
                detail = metric_data.get("detail", "")
                estimated = 1 if metric_data.get("estimated") else 0

                cache_key = (provider_key, metric_key)
                prev = self._last_logged.get(cache_key)

                should_log = False
                if prev is None:
                    should_log = True
                else:
                    prev_time, prev_pct, prev_status = prev
                    if prev_pct != rem_pct or prev_status != status:
                        should_log = True
                    elif (now - prev_time) >= force_heartbeat_seconds:
                        should_log = True

                if should_log:
                    self._last_logged[cache_key] = (now, rem_pct, status)
                    reset_str = json.dumps(reset_at, ensure_ascii=False) if reset_at is not None else ""
                    rows.append(
                        (
                            now,
                            provider_key,
                            metric_key,
                            status,
                            rem_pct,
                            reset_str,
                            detail,
                            estimated,
                            source_age,
                        )
                    )

        if not rows:
            return 0

        with closing(self._connect()) as conn:
            conn.executemany(
                "INSERT INTO snapshots "
                "(collected_at, provider, metric, status, remaining_pct, reset_at, detail, estimated, source_age_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)

    def prune(self, retention_days: int, now: float | None = None) -> int:
        cutoff = (time.time() if now is None else now) - max(1, retention_days) * 86400
        with closing(self._connect()) as conn:
            cursor = conn.execute("DELETE FROM snapshots WHERE collected_at < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount

    def recent(self, provider: str | None = None, limit: int = 100) -> list[tuple]:
        limit = max(1, min(10_000, int(limit)))
        with closing(self._connect()) as conn:
            if provider:
                cursor = conn.execute(
                    "SELECT collected_at, provider, metric, status, remaining_pct, reset_at, detail, estimated "
                    "FROM snapshots WHERE provider = ? ORDER BY collected_at DESC LIMIT ?",
                    (provider, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT collected_at, provider, metric, status, remaining_pct, reset_at, detail, estimated "
                    "FROM snapshots ORDER BY collected_at DESC LIMIT ?",
                    (limit,),
                )
            return cursor.fetchall()

    def query_history(
        self,
        provider: str,
        metric: str,
        range_seconds: float = 21600.0,
        max_points: int = 120,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Query downsampled historical time-series points with burn-rate and ETA calculations."""
        current_time = time.time() if now is None else now
        cutoff = current_time - max(60.0, float(range_seconds))

        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "SELECT collected_at, remaining_pct, status, reset_at, detail "
                "FROM snapshots "
                "WHERE provider = ? AND metric = ? AND collected_at >= ? "
                "ORDER BY collected_at ASC",
                (provider, metric, cutoff),
            )
            raw_rows = cursor.fetchall()

        if not raw_rows:
            return {
                "provider": provider,
                "metric": metric,
                "range_seconds": range_seconds,
                "points": [],
                "burn_rate_pct_hr": 0.0,
                "eta_seconds": None,
                "current_pct": None,
                "total_points": 0,
            }

        total_points = len(raw_rows)
        # Downsample if total points exceed max_points
        if total_points <= max_points:
            selected_rows = raw_rows
        else:
            selected_rows = [raw_rows[0]]
            inner_rows = raw_rows[1:-1]
            num_buckets = max_points - 2
            bucket_size = len(inner_rows) / num_buckets
            for i in range(num_buckets):
                idx = int(i * bucket_size)
                selected_rows.append(inner_rows[idx])
            selected_rows.append(raw_rows[-1])

        points: list[dict[str, Any]] = []
        for r in selected_rows:
            reset_val = None
            if r[3]:
                try:
                    reset_val = json.loads(r[3])
                except Exception:
                    reset_val = r[3]
            points.append(
                {
                    "t": round(r[0], 1),
                    "pct": r[1],
                    "status": r[2],
                    "reset_at": reset_val,
                    "detail": r[4],
                }
            )

        # Calculate Burn Rate (% per hour) and ETA
        valid_points = [(r[0], r[1]) for r in raw_rows if r[1] is not None]
        current_pct = valid_points[-1][1] if valid_points else None
        burn_rate_pct_hr = 0.0
        eta_seconds = None

        if len(valid_points) >= 2:
            total_consumed_pct = 0.0
            time_start = valid_points[0][0]
            time_end = valid_points[-1][0]
            time_span_seconds = time_end - time_start

            for i in range(len(valid_points) - 1):
                p_prev = valid_points[i][1]
                p_curr = valid_points[i + 1][1]
                if p_prev > p_curr:
                    total_consumed_pct += (p_prev - p_curr)

            if time_span_seconds >= 60.0 and total_consumed_pct > 0:
                burn_rate_pct_hr = round((total_consumed_pct / time_span_seconds) * 3600.0, 2)

            if burn_rate_pct_hr > 0 and current_pct is not None and current_pct > 0:
                hours_remaining = current_pct / burn_rate_pct_hr
                eta_seconds = round(hours_remaining * 3600.0)

        return {
            "provider": provider,
            "metric": metric,
            "range_seconds": range_seconds,
            "points": points,
            "burn_rate_pct_hr": burn_rate_pct_hr,
            "eta_seconds": eta_seconds,
            "current_pct": current_pct,
            "total_points": total_points,
        }

    def get_available_metrics(self) -> list[dict[str, str]]:
        """Return list of distinct provider and metric pairs recorded in history."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "SELECT DISTINCT provider, metric FROM snapshots ORDER BY provider, metric"
            )
            rows = cursor.fetchall()
        return [{"provider": r[0], "metric": r[1]} for r in rows]

    def evaluate_alerts(
        self,
        dashboard: DashboardSnapshot,
        threshold_pct: int,
        recovery_pct: int,
        cooldown_seconds: float,
        now: float | None = None,
    ) -> list[AlertEvent]:
        now = time.time() if now is None else now
        threshold_pct = max(0, min(100, int(threshold_pct)))
        recovery_pct = max(threshold_pct + 1, min(100, int(recovery_pct)))
        cooldown_seconds = max(0, float(cooldown_seconds))
        events: list[AlertEvent] = []

        with closing(self._connect()) as conn:
            for provider, snapshot in dashboard.providers().items():
                for metric_name, metric in snapshot.metrics.items():
                    if metric.remaining_pct is None:
                        continue
                    row = conn.execute(
                        "SELECT active, last_notified_at FROM alert_state WHERE provider = ? AND metric = ?",
                        (provider, metric_name),
                    ).fetchone()
                    active = bool(row[0]) if row else False
                    last_notified = row[1] if row and row[1] is not None else None
                    if metric.remaining_pct <= threshold_pct:
                        should_notify = not active or last_notified is None or now - last_notified >= cooldown_seconds
                        if should_notify:
                            events.append(AlertEvent(provider, metric_name, metric.remaining_pct, "low", "Cota baixa"))
                            active = True
                            last_notified = now
                    elif metric.remaining_pct > recovery_pct and active:
                        events.append(AlertEvent(provider, metric_name, metric.remaining_pct, "recovered", "Cota recuperada"))
                        active = False
                    conn.execute(
                        "INSERT INTO alert_state(provider, metric, active, last_notified_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(provider, metric) DO UPDATE SET active=excluded.active, last_notified_at=excluded.last_notified_at",
                        (provider, metric_name, int(active), last_notified),
                    )
            conn.commit()
        return events
