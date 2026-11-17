from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timezone

from fake_sidecar import FakeSidecar, Reply

import spectral_sdk as spectral


def lease(ttl: float) -> spectral.Lease:
    return spectral.Lease(token="abc123", ttl=ttl, expires_at=datetime.now(timezone.utc))


class KeepAliveTest(unittest.TestCase):
    def client(self, responder) -> spectral.Client:
        fake = FakeSidecar(responder)
        self.addCleanup(fake.close)
        return spectral.Client(fake.url, timeout=2)

    def test_renews_until_stopped(self) -> None:
        renewed = threading.Semaphore(0)

        def responder(req):
            renewed.release()
            return Reply(202)

        keeper = self.client(responder).keep_alive_in_background(lease(0.06))
        for i in range(3):
            self.assertTrue(renewed.acquire(timeout=2), f"renew {i + 1} never arrived")

        keeper.stop(timeout=2)
        self.assertFalse(keeper.is_alive())
        self.assertIsNone(keeper.error)

    def test_stops_when_lease_rejected(self) -> None:
        client = self.client(lambda req: Reply(404, text="unknown lease"))
        with self.assertRaises(spectral.LeaseExpiredError):
            client.keep_alive(lease(0.06))

    def test_gives_up_after_ttl(self) -> None:
        client = self.client(lambda req: Reply(500, text="boom"))
        start = time.monotonic()
        with self.assertRaises(spectral.LeaseExpiredError) as ctx:
            client.keep_alive(lease(0.06))
        self.assertGreaterEqual(time.monotonic() - start, 0.06)
        self.assertIsInstance(ctx.exception.__cause__, spectral.APIError)

    def test_retries_transient_failure(self) -> None:
        calls = []
        renewed = threading.Event()

        def responder(req):
            calls.append(req)
            if len(calls) == 1:
                return Reply(500, text="blip")
            renewed.set()
            return Reply(202)

        with self.client(responder).keep_alive_in_background(lease(0.06)) as keeper:
            self.assertTrue(renewed.wait(timeout=2))
            self.assertIsNone(keeper.error)

    def test_background_records_error(self) -> None:
        keeper = self.client(lambda req: Reply(404, text="unknown lease")).keep_alive_in_background(lease(0.06))
        keeper._thread.join(timeout=2)
        self.assertIsInstance(keeper.error, spectral.LeaseExpiredError)

    def test_rejects_zero_ttl(self) -> None:
        client = spectral.Client("http://127.0.0.1:1")
        with self.assertRaises(ValueError):
            client.keep_alive(lease(0))


if __name__ == "__main__":
    unittest.main()
