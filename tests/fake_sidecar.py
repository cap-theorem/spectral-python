from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit


@dataclass
class Request:
    method: str
    path: str
    query: dict
    headers: dict
    body: Any


@dataclass
class Reply:
    status: int
    body: Any = None
    text: str | None = None


Responder = Callable[[Request], Reply]


@dataclass
class FakeSidecar:
    responder: Responder
    requests: list = field(default_factory=list)

    def __post_init__(self) -> None:
        sidecar = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                self._handle()

            def do_POST(self) -> None:
                self._handle()

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                url = urlsplit(self.path)
                request = Request(
                    method=self.command,
                    path=url.path,
                    query={k: v[0] for k, v in parse_qs(url.query).items()},
                    headers=dict(self.headers.items()),
                    body=json.loads(raw) if raw else None,
                )
                sidecar.requests.append(request)

                reply = sidecar.responder(request)
                if reply.text is not None:
                    payload = reply.text.encode("utf-8")
                    content_type = "text/plain; charset=utf-8"
                elif reply.body is not None:
                    payload = json.dumps(reply.body).encode("utf-8")
                    content_type = "application/json"
                else:
                    payload = b""
                    content_type = "text/plain"

                self.send_response(reply.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
