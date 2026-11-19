from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from spectral_sdk._wire import format_duration, parse_duration, parse_rfc3339
from spectral_sdk.errors import (
    LeaseExpiredError,
    SidecarUnavailableError,
    SpectralError,
    api_error,
)

__version__ = "0.1.0"

DEFAULT_ADDR = "http://127.0.0.1:8000"
ADDR_ENV = "SPECTRAL_ADDR"
DEFAULT_TIMEOUT = 10.0

_MAX_ERROR_BODY = 4096


@dataclass(frozen=True)
class Lease:
    token: str
    ttl: float
    expires_at: datetime


@dataclass(frozen=True)
class Provider:
    service: str
    instance_id: str
    endpoint: str


@dataclass(frozen=True)
class Status:
    ready: bool
    state: str


class Client:
    def __init__(self, addr: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        resolved = addr or os.environ.get(ADDR_ENV) or DEFAULT_ADDR
        resolved = resolved.rstrip("/")
        parsed = urllib.parse.urlsplit(resolved)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"spectral: addr {resolved!r} must be an http(s) URL with a host")

        self._addr = resolved
        self._timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def addr(self) -> str:
        return self._addr

    def register(self, service: str, instance_id: str, endpoint: str, ttl: float) -> Lease:
        body = {
            "service": service,
            "instance_id": instance_id,
            "endpoint": endpoint,
            "ttl": format_duration(ttl),
        }
        data = self._do("register", "POST", "/register", body=body)
        try:
            return Lease(
                token=data["token"],
                ttl=parse_duration(data["ttl"]),
                expires_at=parse_rfc3339(data["expires_at"]),
            )
        except (KeyError, TypeError, ValueError) as err:
            raise SpectralError(f"spectral register: invalid response: {err}") from err

    def renew(self, token: str) -> None:
        self._do("renew", "POST", "/renew", body={"token": token})

    def lookup(self, service: str) -> Provider:
        data = self._do("lookup", "GET", "/lookup", query={"service": service})
        try:
            return Provider(
                service=data["service"],
                instance_id=data.get("instance_id", ""),
                endpoint=data["endpoint"],
            )
        except (KeyError, TypeError) as err:
            raise SpectralError(f"spectral lookup: invalid response: {err}") from err

    def status(self) -> Status:
        data = self._do("status", "GET", "/status")
        try:
            return Status(ready=bool(data["ready"]), state=data["state"])
        except (KeyError, TypeError) as err:
            raise SpectralError(f"spectral status: invalid response: {err}") from err

    def keep_alive(self, lease: Lease, stop: threading.Event | None = None) -> None:
        interval = lease.ttl / 3
        if interval <= 0:
            raise ValueError(f"spectral keep_alive: lease ttl {lease.ttl}s is too short")

        if stop is None:
            stop = threading.Event()

        deadline = time.monotonic() + lease.ttl
        while not stop.wait(interval):
            now = time.monotonic()
            try:
                self.renew(lease.token)
            except LeaseExpiredError:
                raise
            except SpectralError as err:
                if now >= deadline:
                    raise LeaseExpiredError(f"spectral keep_alive: lease expired, last renew failed: {err}") from err
                continue
            deadline = now + lease.ttl

    def keep_alive_in_background(self, lease: Lease) -> KeepAliveThread:
        keeper = KeepAliveThread(self, lease)
        keeper.start()
        return keeper

    def _do(
        self,
        op: str,
        method: str,
        path: str,
        query: dict | None = None,
        body: dict | None = None,
    ) -> Any:
        url = self._addr + path
        if query:
            url += "?" + urllib.parse.urlencode(query)

        headers = {
            "Accept": "application/json",
            "User-Agent": f"spectral-python/{__version__}",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as err:
            message = err.read(_MAX_ERROR_BODY).decode("utf-8", "replace").strip()
            err.close()
            failure = api_error(op, err.code, message)
            raise failure from failure.__cause__
        except (urllib.error.URLError, OSError) as err:
            reason = getattr(err, "reason", err)
            raise SidecarUnavailableError(f"spectral {op}: sidecar unavailable at {self._addr}: {reason}") from err

        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as err:
            raise SpectralError(f"spectral {op}: invalid JSON response") from err


class KeepAliveThread:
    def __init__(self, client: Client, lease: Lease) -> None:
        self.error: SpectralError | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(client, lease),
            name="spectral-keepalive",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def __enter__(self) -> KeepAliveThread:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _run(self, client: Client, lease: Lease) -> None:
        try:
            client.keep_alive(lease, self._stop)
        except SpectralError as err:
            self.error = err
