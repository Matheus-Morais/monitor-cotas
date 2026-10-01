"""Telemetry service orchestrating all providers with anti-flicker caching."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Sequence

from providers.antigravity import AntigravityProvider
from providers.base import BaseProvider
from providers.claude import ClaudeProvider
from providers.codex import CodexProvider
from quota_core import ProviderSnapshot
from services.profiles import ProfileService

DEFAULT_ANTI_FLICKER_SECONDS = 30.0


def serialize_snapshot(
    snapshot: ProviderSnapshot | None,
    active_email: str = "",
    is_stale: bool = False,
) -> dict[str, Any]:
    """Serialize a ProviderSnapshot into the dictionary structure expected by the WebView UI."""
    if not snapshot:
        return {"status": "unavailable", "metrics": {}}

    data: dict[str, Any] = {
        "provider": snapshot.provider,
        "status": snapshot.status,
        "model": snapshot.model,
        "plan": snapshot.plan,
        "account": snapshot.account,
        "is_active_account": bool(snapshot.account and active_email and snapshot.account == active_email),
        "source_age_seconds": snapshot.source_age_seconds,
        "is_stale": is_stale,
        "metrics": {
            k: {
                "remaining_pct": m.remaining_pct,
                "reset_at": m.reset_at,
                "detail": m.detail,
                "estimated": m.estimated,
            }
            for k, m in snapshot.metrics.items()
        },
    }
    return data


class TelemetryService:
    """Manages telemetry collection across all AI providers with anti-flicker resilience."""

    def __init__(
        self,
        providers: Sequence[BaseProvider] | None = None,
        profile_service: ProfileService | None = None,
        refresh_interval: float = 3.0,
        agy_poll_interval: float = 120.0,
        anti_flicker_seconds: float = DEFAULT_ANTI_FLICKER_SECONDS,
    ):
        self.profile_service = profile_service or ProfileService()
        self.anti_flicker_seconds = anti_flicker_seconds
        self.refresh_interval = max(1.0, float(refresh_interval))
        self.agy_poll_interval = max(10.0, float(agy_poll_interval))

        if providers is not None:
            self.providers = list(providers)
        else:
            self.providers = [
                AntigravityProvider(),
                ClaudeProvider(1, active_email_getter=self.profile_service.get_active_email),
                ClaudeProvider(2, active_email_getter=self.profile_service.get_active_email),
                CodexProvider(),
            ]

        self._lock = threading.RLock()
        self._last_snapshots: dict[str, ProviderSnapshot] = {}
        self._last_good_snapshots: dict[str, tuple[ProviderSnapshot, float]] = {}
        self._latest_serialized: dict[str, Any] = {}
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

        self._running = False
        self._poller_thread: threading.Thread | None = None
        self._agy_thread: threading.Thread | None = None

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Add a callback invoked whenever new telemetry snapshots are collected."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def collect(self) -> dict[str, Any]:
        """Collect snapshots from all providers with anti-flicker caching."""
        active_email = self.profile_service.get_active_email()
        results: dict[str, Any] = {}
        now = time.monotonic()

        with self._lock:
            for provider in self.providers:
                is_stale = False
                try:
                    snapshot = provider.collect()
                except Exception as exc:
                    snapshot = ProviderSnapshot(
                        provider=provider.key,
                        status="unavailable",
                        metrics={},
                        metadata={"error": str(exc)},
                    )

                # Check if snapshot is valid (ok or warn)
                if snapshot and snapshot.status != "unavailable":
                    self._last_good_snapshots[provider.key] = (snapshot, now)
                else:
                    # Check anti-flicker cache for transient failures
                    cached = self._last_good_snapshots.get(provider.key)
                    if cached:
                        cached_snapshot, timestamp = cached
                        if (now - timestamp) <= self.anti_flicker_seconds:
                            snapshot = cached_snapshot
                            is_stale = True

                self._last_snapshots[provider.key] = snapshot
                results[provider.key] = serialize_snapshot(snapshot, active_email, is_stale=is_stale)

            self._latest_serialized = dict(results)
            listeners = list(self._listeners)

        for listener in listeners:
            try:
                listener(results)
            except Exception:
                pass

        return results

    def get_latest(self) -> dict[str, Any]:
        """Get the cached latest serialized snapshots without triggering collection."""
        with self._lock:
            if not self._latest_serialized:
                return self.collect()
            return dict(self._latest_serialized)

    def force_refresh(self) -> dict[str, Any]:
        """Trigger on-demand provider refresh (e.g. agy) and immediately collect."""
        for provider in self.providers:
            provider.trigger_refresh()
        return self.collect()

    def start(self) -> None:
        """Start background polling threads."""
        with self._lock:
            if self._running:
                return
            self._running = True

            self._poller_thread = threading.Thread(
                target=self._background_poller,
                daemon=True,
                name="telemetry-poller",
            )
            self._poller_thread.start()

            self._agy_thread = threading.Thread(
                target=self._background_agy_poller,
                daemon=True,
                name="agy-usage-poller",
            )
            self._agy_thread.start()

    def stop(self) -> None:
        """Stop background polling threads."""
        with self._lock:
            self._running = False

    def _background_poller(self) -> None:
        while self._running:
            try:
                self.collect()
            except Exception:
                pass
            time.sleep(self.refresh_interval)

    def _background_agy_poller(self) -> None:
        while self._running:
            time.sleep(self.agy_poll_interval)
            if not self._running:
                break
            for provider in self.providers:
                if provider.key == "agy":
                    provider.trigger_refresh()
