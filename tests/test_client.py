from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fake_sidecar import FakeSidecar, Reply

import spectral_sdk as spectral
from spectral_sdk._wire import format_duration, parse_duration, parse_rfc3339


class SidecarTestCase(unittest.TestCase):
    def sidecar(self, responder) -> spectral.Client:
        fake = FakeSidecar(responder)
        self.addCleanup(fake.close)
        self.fake = fake
        return spectral.Client(fake.url)


class AddrTest(unittest.TestCase):
    def test_resolution(self) -> None:
        cases = [
            ("default", None, None, spectral.DEFAULT_ADDR),
            ("env", None, "http://10.0.0.5:8007", "http://10.0.0.5:8007"),
            ("explicit beats env", "http://127.0.0.1:8003", "http://10.0.0.5:8007", "http://127.0.0.1:8003"),
            ("trailing slash", "http://127.0.0.1:8003/", None, "http://127.0.0.1:8003"),
        ]
        for name, addr, env, want in cases:
            with self.subTest(name):
                environ = {k: v for k, v in os.environ.items() if k != spectral.ADDR_ENV}
                if env is not None:
                    environ[spectral.ADDR_ENV] = env
                with mock.patch.dict(os.environ, environ, clear=True):
                    self.assertEqual(spectral.Client(addr).addr, want)

    def test_rejects_bad_addr(self) -> None:
        for addr in ["127.0.0.1:8000", "ftp://127.0.0.1:8000", "http://"]:
            with self.subTest(addr), self.assertRaises(ValueError):
                spectral.Client(addr)


class RegisterTest(SidecarTestCase):
    def test_register(self) -> None:
        client = self.sidecar(
            lambda req: Reply(
                201,
                {"token": "abc123", "ttl": "10s", "expires_at": "2026-11-16T12:00:10.123456789-08:00"},
            )
        )

        lease = client.register(
            service="payments",
            instance_id="payments-1",
            endpoint="http://100.64.1.3:9007",
            ttl=10,
        )

        req = self.fake.requests[0]
        self.assertEqual((req.method, req.path), ("POST", "/register"))
        self.assertEqual(req.headers["Content-Type"], "application/json")
        self.assertEqual(
            req.body,
            {"service": "payments", "instance_id": "payments-1", "endpoint": "http://100.64.1.3:9007", "ttl": "10s"},
        )
        self.assertEqual(lease.token, "abc123")
        self.assertEqual(lease.ttl, 10.0)
        want = datetime(2026, 11, 16, 12, 0, 10, 123456, tzinfo=timezone(timedelta(hours=-8)))
        self.assertEqual(lease.expires_at, want)

    def test_errors(self) -> None:
        cases = [
            (400, spectral.InvalidRegistrationError),
            (409, spectral.AlreadyRegisteredError),
            (504, spectral.SidecarTimeoutError),
            (500, spectral.APIError),
        ]
        for status, want in cases:
            with self.subTest(status):
                client = self.sidecar(lambda req, status=status: Reply(status, text="nope\n"))
                with self.assertRaises(want) as ctx:
                    client.register("payments", "p1", "http://127.0.0.1:9000", ttl=1)
                err = ctx.exception
                self.assertIsInstance(err, spectral.APIError)
                self.assertEqual((err.op, err.status_code, err.message), ("register", status, "nope"))
                self.assertIs(type(err), want)


class RenewTest(SidecarTestCase):
    def test_renew_sends_token(self) -> None:
        client = self.sidecar(lambda req: Reply(202))
        client.renew("abc123")
        req = self.fake.requests[0]
        self.assertEqual((req.method, req.path, req.body), ("POST", "/renew", {"token": "abc123"}))

    def test_unknown_token(self) -> None:
        client = self.sidecar(lambda req: Reply(404, text="unknown lease"))
        with self.assertRaises(spectral.LeaseExpiredError) as ctx:
            client.renew("gone")
        self.assertIsInstance(ctx.exception.__cause__, spectral.APIError)


class LookupTest(SidecarTestCase):
    def test_lookup(self) -> None:
        client = self.sidecar(
            lambda req: Reply(
                200,
                {"service": "model-server/llama 70b", "instance_id": "ms-0412", "endpoint": "http://100.64.1.6:9002"},
            )
        )

        provider = client.lookup("model-server/llama 70b")

        req = self.fake.requests[0]
        self.assertEqual((req.method, req.path), ("GET", "/lookup"))
        self.assertEqual(req.query, {"service": "model-server/llama 70b"})
        self.assertEqual(
            provider,
            spectral.Provider("model-server/llama 70b", "ms-0412", "http://100.64.1.6:9002"),
        )

    def test_not_found(self) -> None:
        client = self.sidecar(lambda req: Reply(404, text="no providers"))
        with self.assertRaises(spectral.ServiceNotFoundError):
            client.lookup("inventory")


class StatusTest(SidecarTestCase):
    def test_status(self) -> None:
        client = self.sidecar(lambda req: Reply(200, {"ready": True, "state": "active"}))
        self.assertEqual(client.status(), spectral.Status(ready=True, state="active"))
        self.assertEqual(self.fake.requests[0].path, "/status")

    def test_base_path_prefix(self) -> None:
        fake = FakeSidecar(lambda req: Reply(200, {"ready": True, "state": "active"}))
        self.addCleanup(fake.close)
        spectral.Client(fake.url + "/sidecar/").status()
        self.assertEqual(fake.requests[0].path, "/sidecar/status")


class UnavailableTest(unittest.TestCase):
    def test_sidecar_down(self) -> None:
        fake = FakeSidecar(lambda req: Reply(200))
        url = fake.url
        fake.close()
        with self.assertRaises(spectral.SidecarUnavailableError):
            spectral.Client(url, timeout=2).status()


class WireTest(unittest.TestCase):
    def test_parse_duration(self) -> None:
        cases = {
            "10s": 10.0,
            "1m30s": 90.0,
            "1.5s": 1.5,
            "250ms": 0.25,
            "1h": 3600.0,
            "0s": 0.0,
            "0": 0.0,
            "-2s": -2.0,
            "500µs": 0.0005,
        }
        for text, want in cases.items():
            with self.subTest(text):
                self.assertAlmostEqual(parse_duration(text), want)

    def test_parse_duration_rejects_garbage(self) -> None:
        for text in ["", "10", "10x", "s", "1s2"]:
            with self.subTest(text), self.assertRaises(ValueError):
                parse_duration(text)

    def test_format_duration(self) -> None:
        self.assertEqual(format_duration(10), "10s")
        self.assertEqual(format_duration(1.5), "1500ms")

    def test_parse_rfc3339(self) -> None:
        self.assertEqual(
            parse_rfc3339("2026-11-16T20:00:10Z"),
            datetime(2026, 11, 16, 20, 0, 10, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
