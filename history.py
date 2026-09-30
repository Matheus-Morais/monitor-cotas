"""Local snapshot history and durable alert state."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

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
