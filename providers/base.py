"""Base telemetry provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from quota_core import ProviderSnapshot


class BaseProvider(ABC):
    """Abstract base class for all quota and telemetry providers."""

    def __init__(self, key: str, display_name: str):
        self.key = key
        self.display_name = display_name

    @abstractmethod
    def collect(self) -> ProviderSnapshot:
        """Collect current quotas and metrics for this provider."""
        raise NotImplementedError

    def get_snapshot(self) -> ProviderSnapshot:
        """Alias for collect() for convenience and backward compatibility."""
        return self.collect()

    @abstractmethod
    def is_available(self) -> bool:
        """Check if source files or CLI tools for this provider exist."""
        raise NotImplementedError

    def trigger_refresh(self) -> bool:
        """Optional on-demand refresh trigger (e.g. running a CLI command)."""
        return False
