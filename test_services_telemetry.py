"""Unit tests for services/telemetry.py and anti-flicker caching."""

import time
import unittest
from unittest.mock import MagicMock

from providers.base import BaseProvider
from quota_core import Metric, ProviderSnapshot
from services.telemetry import TelemetryService, serialize_snapshot


class DummyProvider(BaseProvider):
    def __init__(self, key="dummy", should_fail=False):
        super().__init__(key=key, display_name="Dummy Provider")
        self.should_fail = should_fail
        self.call_count = 0
        self.refresh_triggered = False

    def is_available(self) -> bool:
        return True

    def collect(self) -> ProviderSnapshot:
        self.call_count += 1
        if self.should_fail:
            raise OSError("File locked or permission denied")
        return ProviderSnapshot(
            provider=self.key,
            status="ok",
            metrics={"m1": Metric(remaining_pct=90)},
            account="user@example.com",
            model="dummy-v1",
        )

    def trigger_refresh(self) -> bool:
        self.refresh_triggered = True
        return True


class TestTelemetryService(unittest.TestCase):
    def test_serialize_snapshot_none(self):
        serialized = serialize_snapshot(None)
        self.assertEqual(serialized["status"], "unavailable")
        self.assertEqual(serialized["metrics"], {})

    def test_serialize_snapshot_active_email(self):
        snap = ProviderSnapshot(
            provider="test",
            status="ok",
            metrics={"test_metric": Metric(remaining_pct=75, reset_at=100)},
            account="dev@domain.com",
        )
        res = serialize_snapshot(snap, active_email="dev@domain.com")
        self.assertTrue(res["is_active_account"])
        self.assertEqual(res["metrics"]["test_metric"]["remaining_pct"], 75)

        res_other = serialize_snapshot(snap, active_email="other@domain.com")
        self.assertFalse(res_other["is_active_account"])

    def test_anti_flicker_caches_transient_failure(self):
        provider = DummyProvider(key="dummy", should_fail=False)
        profile_service = MagicMock()
        profile_service.get_active_email.return_value = "user@example.com"

        service = TelemetryService(
            providers=[provider],
            profile_service=profile_service,
            anti_flicker_seconds=0.1,
        )

        # 1. Normal successful collection
        data1 = service.collect()
        self.assertEqual(data1["dummy"]["status"], "ok")
        self.assertFalse(data1["dummy"]["is_stale"])

        # 2. Transient failure (simulate lock / read error)
        provider.should_fail = True
        data2 = service.collect()
        # Should still be ok due to anti-flicker cache, but flagged as is_stale
        self.assertEqual(data2["dummy"]["status"], "ok")
        self.assertTrue(data2["dummy"]["is_stale"])

        # 3. Wait past anti-flicker expiration (0.1s)
        time.sleep(0.15)
        data3 = service.collect()
        # Now it should be unavailable
        self.assertEqual(data3["dummy"]["status"], "unavailable")

    def test_listeners_notification(self):
        provider = DummyProvider(key="dummy", should_fail=False)
        service = TelemetryService(providers=[provider])

        received = []
        listener = lambda data: received.append(data)
        service.add_listener(listener)

        service.collect()
        self.assertEqual(len(received), 1)
        self.assertIn("dummy", received[0])

        service.remove_listener(listener)
        service.collect()
        self.assertEqual(len(received), 1)  # No new callback

    def test_force_refresh_triggers_provider(self):
        provider = DummyProvider(key="dummy", should_fail=False)
        service = TelemetryService(providers=[provider])
        self.assertFalse(provider.refresh_triggered)

        service.force_refresh()
        self.assertTrue(provider.refresh_triggered)


if __name__ == "__main__":
    unittest.main()
