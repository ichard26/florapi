# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Optional, Union, cast

if TYPE_CHECKING:
    ASGI3Application = Any
    ASGIReceiveCallable = Any
    ASGISendCallable = Any
    Headers = dict[bytes, bytes]
    HTTPScope = Any
    Scope = Any
    WebSocketScope = Any

from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from florapi import utc_now
from florapi.sqlite import SQLiteConnection

LOG_SCHEMA = {
    "datetime":   "TEXT PRIMARY KEY NOT NULL",
    "ip":         "TEXT",
    "useragent":  "TEXT",
    "referer":    "TEXT",
    "verb":       "TEXT NOT NULL",
    "path":       "TEXT NOT NULL",
    "status":     "INTEGER NOT NULL",
    "duration":   "REAL NOT NULL"
}


class TimedLogMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        sqlite_factory: Callable[[], SQLiteConnection],
        sqlite_table: str = "requests",
        sqlite_autoclose: bool = True,
        extra_columns: dict[str, str] = {},  # noqa: B006
        hook: Callable[[Request, Response, dict], None] = (lambda *_: None),
    ) -> None:
        super().__init__(app)
        self.sqlite_table = sqlite_table
        self.sqlite_factory = sqlite_factory
        self.sqlite_schema = {**LOG_SCHEMA, **extra_columns}
        self.sqlite_autoclose = sqlite_autoclose
        self.hook = hook

    async def dispatch(self, request: Request, call_next: Awaitable) -> Response:
        start_time = time.perf_counter()
        response = await call_next(request)
        elapsed = round((time.perf_counter() - start_time) * 1000, 2)

        def insert_in(db: SQLiteConnection) -> None:
            with db:
                db.insert(self.sqlite_table, entry)

        async def log() -> None:
            db = self.sqlite_factory()
            try:
                try:
                    insert_in(db)
                except sqlite3.DatabaseError:
                    if db.existing_table(self.sqlite_table):
                        raise

                    db.create_table(self.sqlite_table, self.sqlite_schema)
                    insert_in(db)
            finally:
                if self.sqlite_autoclose:
                    db.close()

        entry = {
            "datetime": utc_now(),
            "ip": getattr(request.client, "host", None),
            "useragent": request.headers.get("User-Agent"),
            "referer": request.headers.get("Referer"),
            "verb": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration": elapsed,
        }
        self.hook(request, response, entry)
        response.background = BackgroundTask(log)
        response.headers["Server-Timing"] = f"endpoint;dur={elapsed:.1f}"
        return response


class ProxyHeadersMiddleware:
    """
    This middleware can be used when a known proxy is fronting the application,
    and is trusted to be properly setting (configurable) proxy headers with
    connecting client information. Depends on X-Forwarded-Proto.

    Modifies the `client` and `scheme` information so that they reference
    the connecting client, rather that the connecting proxy.

    Portions taken from the encode/uvicorn project. License can be found in
    :/LICENSE-THIRDPARTY.md

    Copyright © 2017-present, Encode OSS Ltd. All rights reserved.
    """

    def __init__(
        self,
        app: "ASGI3Application",
        ip_header: bytes = b"x-real-ip",
        port_header: bytes = b"x-real-port",
        require_none_client: bool = False,
    ) -> None:
        self.app = app
        self.ip_header = ip_header
        self.port_header = port_header
        self.trusted_clients = (None,) if require_none_client else ("127.0.0.1", "localhost")

    def rewrite_scheme(self, scope: "Scope", headers: "Headers") -> None:
        # Determine if the incoming request was http or https based on
        # the X-Forwarded-Proto header.
        x_forwarded_proto = (headers[b"x-forwarded-proto"].decode("latin1").strip())
        if scope["type"] == "websocket":
            scope["scheme"] = "wss" if x_forwarded_proto == "https" else "ws"
        else:
            scope["scheme"] = x_forwarded_proto

    def rewrite_client(
        self, scope: "Scope", headers: "Headers", client_host: Optional[str]
    ) -> None:
        host = headers[self.ip_header].decode("latin1")
        port = int(headers[self.port_header].decode("latin1"))
        scope["client"] = (host, port)

    async def __call__(
        self, scope: "Scope", receive: "ASGIReceiveCallable", send: "ASGISendCallable"
    ) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = cast(Union["HTTPScope", "WebSocketScope"], scope)
            headers = dict(scope["headers"])
            client_addr: Optional[tuple[str, int]] = scope.get("client")
            client_host = client_addr[0] if client_addr else None

            if client_host in self.trusted_clients:
                if b"x-forwarded-proto" in headers:
                    self.rewrite_scheme(scope, headers)
                if self.ip_header in headers:
                    self.rewrite_client(scope, headers, client_host)

        return await self.app(scope, receive, send)
