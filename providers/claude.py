"""Anthropic Claude Code telemetry provider.

Supports multi-tiered telemetry extraction:
1. Live direct API queries to Anthropic (`GET /api/oauth/usage`) using local OAuth token.
2. Real-time statusline session input (`claude-statusline-input.json`) for live context window,
   active model (e.g. Opus 5.5), and session rate limits.
3. Persistent profiles (`~/.claude-{account_num}.json`) and active config (`~/.claude.json`).
4. Automatic reset detection and file cache synchronization.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from providers.base import BaseProvider
from quota_core import ERROR, UNAVAILABLE, Metric, ProviderSnapshot, load_claude_snapshot

DEFAULT_CLAUDE_STATUS = Path.home() / "scripts" / "claude-statusline-input.json"
DEFAULT_ACTIVE_PATH = Path.home() / ".claude.json"
DEFAULT_CREDENTIALS_DIR = Path.home() / ".claude"


def fetch_claude_api_usage(token: str, timeout: float = 4.0) -> dict[str, Any] | None:
    """Fetch live rate limits from Anthropic / Claude Code API."""
    if not token or not token.strip():
        return None

    endpoints = [
        "https://api.anthropic.com/api/oauth/usage",
        "https://claude.ai/api/oauth/usage",
    ]
    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "User-Agent": "Claude-Code/2.1.287",
        "Accept": "application/json",
    }

    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    raw = resp.read()
                    data = json.loads(raw.decode("utf-8"))
                    if isinstance(data, dict) and ("five_hour" in data or "seven_day" in data):
                        return data
        except Exception:
            continue

    return None


def read_oauth_token(credentials_path: Path | str) -> str:
    path = Path(credentials_path)
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("claudeAiOauth", {}).get("accessToken") or "")
    except Exception:
        return ""


class ClaudeProvider(BaseProvider):
    """Telemetry provider for Claude Code profile accounts."""

    def __init__(
        self,
        account_num: int = 1,
        profile_path: Path | str | None = None,
        status_path: Path | str | None = None,
        active_path: Path | str | None = None,
        credentials_path: Path | str | None = None,
        active_email_getter: Callable[[], str] | None = None,
        key: str | None = None,
        display_name: str | None = None,
        api_cache_ttl: float = 60.0,
    ):
        self.account_num = account_num
        provider_key = key or f"claude{account_num}"
        provider_name = display_name or f"Claude (Conta {account_num})"
        super().__init__(key=provider_key, display_name=provider_name)

        home = Path.home()
        if profile_path:
            self.profile_path = Path(profile_path)
            custom_home = self.profile_path.parent
        else:
            self.profile_path = home / f".claude-{account_num}.json"
            custom_home = home

        if status_path:
            self.status_path = Path(status_path)
        else:
            env_val = os.environ.get("CLAUDE_STATUS_JSON")
            if env_val:
                self.status_path = Path(env_val)
            elif custom_home == home:
                self.status_path = DEFAULT_CLAUDE_STATUS
            else:
                self.status_path = custom_home / "scripts" / "claude-statusline-input.json"

        if active_path:
            self.active_path = Path(active_path)
        else:
            self.active_path = custom_home / ".claude.json" if custom_home != home else DEFAULT_ACTIVE_PATH

        self.active_email_getter = active_email_getter
        self.api_cache_ttl = api_cache_ttl

        if credentials_path:
            self.credentials_path = Path(credentials_path)
        else:
            claude_dir = custom_home / ".claude" if custom_home != home else (home / ".claude")
            prof_cred = claude_dir / f".credentials-{account_num}.json"
            self.credentials_path = prof_cred if prof_cred.exists() else (claude_dir / ".credentials.json")

        self._cached_api_usage: dict[str, Any] | None = None
        self._cached_api_time: float = 0.0
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        if self.account_num == 1:
            return self.profile_path.exists() or self.status_path.exists() or self.active_path.exists()
        return self.profile_path.exists()

    def _get_profile_email(self) -> str:
        target = self.profile_path if self.profile_path.exists() else self.active_path
        if not target.exists():
            return ""
        try:
            content = target.read_text(encoding="utf-8")
            data = json.loads(content)
            return str(data.get("oauthAccount", {}).get("emailAddress", "") or "")
        except Exception:
            return ""

    def _sync_active_cache(self, usage_data: dict[str, Any]) -> None:
        """Keep cachedUsageUtilization synchronized in active .claude.json and profile."""
        for target in (self.active_path, self.profile_path):
            if not target.exists():
                continue
            try:
                content = target.read_text(encoding="utf-8")
                doc = json.loads(content)
                account_uuid = doc.get("oauthAccount", {}).get("accountUuid", "")
                doc["cachedUsageUtilization"] = {
                    "fetchedAtMs": int(time.time() * 1000),
                    "accountUuid": account_uuid,
                    "utilization": usage_data,
                }
                target.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

    def _fetch_and_cache_api(self, force: bool = False, is_active: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            if not force and self._cached_api_usage is not None and (now - self._cached_api_time) < self.api_cache_ttl:
                return self._cached_api_usage

        # Look for credentials
        claude_dir = self.credentials_path.parent
        per_account_cred = claude_dir / f".credentials-{self.account_num}.json"
        main_cred = claude_dir / ".credentials.json"

        token = ""
        if is_active and main_cred.exists():
            token = read_oauth_token(main_cred)
        elif per_account_cred.exists():
            token = read_oauth_token(per_account_cred)
        elif self.credentials_path.exists():
            token = read_oauth_token(self.credentials_path)

        if not token:
            return None

        data = fetch_claude_api_usage(token)
        if data:
            with self._lock:
                self._cached_api_usage = data
                self._cached_api_time = now

            if is_active:
                self._sync_active_cache(data)

            return data

        return None

    def trigger_refresh(self) -> bool:
        """Trigger an immediate background refresh of Claude usage."""
        with self._lock:
            self._cached_api_time = 0.0

        def _do_fetch():
            active_email = self.active_email_getter() if self.active_email_getter else ""
            profile_email = self._get_profile_email()
            is_active = bool(active_email and profile_email and profile_email == active_email)
            self._fetch_and_cache_api(force=True, is_active=is_active)

        threading.Thread(target=_do_fetch, daemon=True, name=f"claude-refresh-{self.account_num}").start()
        return True

    def collect(self) -> ProviderSnapshot:
        try:
            active_email = self.active_email_getter() if self.active_email_getter else ""
            profile_email = self._get_profile_email()

            is_active = bool(active_email and profile_email and profile_email == active_email)
            if not is_active and self.account_num == 1 and not self.profile_path.exists() and self.active_path.exists():
                is_active = True

            # Determine primary file for identity & base snapshot
            primary_file = self.active_path if (is_active and self.active_path.exists()) else self.profile_path
            if not primary_file.exists() and self.active_path.exists():
                primary_file = self.active_path

            base_snap: ProviderSnapshot | None = None
            if primary_file.exists():
                try:
                    base_snap = load_claude_snapshot(str(primary_file))
                except Exception:
                    base_snap = None

            # Attempt live API usage
            api_data = self._fetch_and_cache_api(force=False, is_active=is_active)
            api_snap: ProviderSnapshot | None = None
            if api_data:
                try:
                    api_snap = load_claude_snapshot(api_data)
                except Exception:
                    api_snap = None

            # Attempt live statusline input (only applies to active session)
            status_snap: ProviderSnapshot | None = None
            if is_active and self.status_path.exists():
                try:
                    status_snap = load_claude_snapshot(str(self.status_path))
                except Exception:
                    status_snap = None

            # Merge results
            account = ""
            plan = ""
            model = ""
            metadata: dict[str, Any] = {"telemetry_available": False}
            metrics: dict[str, Metric] = {}

            if base_snap:
                account = base_snap.account
                plan = base_snap.plan
                model = base_snap.model
                metadata.update(base_snap.metadata)
                metrics.update(base_snap.metrics)

            if api_snap and api_snap.metrics:
                if "five_hour" in api_snap.metrics and api_snap.metrics["five_hour"].remaining_pct is not None:
                    metrics["five_hour"] = api_snap.metrics["five_hour"]
                if "seven_day" in api_snap.metrics and api_snap.metrics["seven_day"].remaining_pct is not None:
                    metrics["seven_day"] = api_snap.metrics["seven_day"]
                metadata["telemetry_available"] = True

            if status_snap:
                if not model and status_snap.model:
                    model = status_snap.model
                elif status_snap.model and not (base_snap and base_snap.model):
                    model = status_snap.model
                if "context" in status_snap.metrics and status_snap.metrics["context"].remaining_pct is not None:
                    metrics["context"] = status_snap.metrics["context"]
                if not api_snap or not api_snap.metrics:
                    if "five_hour" in status_snap.metrics:
                        metrics["five_hour"] = status_snap.metrics["five_hour"]
                    if "seven_day" in status_snap.metrics:
                        metrics["seven_day"] = status_snap.metrics["seven_day"]
                    metadata["telemetry_available"] = True
                if "cost_usd" in status_snap.metadata:
                    metadata["cost_usd"] = status_snap.metadata["cost_usd"]

            has_metrics = any(m.remaining_pct is not None for m in metrics.values())
            if has_metrics:
                status = "ok"
                metadata["telemetry_available"] = True
                error = ""
            elif base_snap and base_snap.status != "unavailable":
                status = base_snap.status
                error = base_snap.error
            else:
                status = "unavailable"
                error = "limites ausentes" if not account else "sem telemetria recente"

            source_age = base_snap.source_age_seconds if base_snap else None
            if api_snap:
                source_age = 0
            elif status_snap and is_active:
                try:
                    source_age = max(0, int(time.time() - self.status_path.stat().st_mtime))
                except Exception:
                    pass

            return ProviderSnapshot(
                provider="claude",
                status=status,
                metrics=metrics,
                model=model,
                plan=plan,
                account=account,
                source_age_seconds=source_age,
                metadata=metadata,
                error=error,
            )
        except Exception as exc:
            return ProviderSnapshot(
                provider="claude",
                status="unavailable",
                metrics={},
                metadata={"error": str(exc)},
            )
