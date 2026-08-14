"""Dependency-free HTTP dashboard server running inside server.app."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from dashboard.routes import DashboardRoutes


LOGGER = logging.getLogger(__name__)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    """Allow immediate restart after Ctrl+C, especially on Windows."""

    allow_reuse_address = True


class DashboardServer:
    def __init__(self, *, host: str, port: int, routes: DashboardRoutes) -> None:
        self._host = host
        self._port = port
        self._routes = routes
        self._httpd: ReusableThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._httpd is None:
            return self._host, self._port
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        routes = self._routes

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                try:
                    status, content_type, body = routes.dispatch(self.path)
                except Exception:
                    LOGGER.exception("Dashboard request gagal: %s", self.path)
                    status = 500
                    content_type = "application/json; charset=utf-8"
                    body = b'{"error":"Internal server error"}'
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    # Browser refresh/close may cancel an in-flight response. This is
                    # normal client behaviour and must not flood the server terminal.
                    LOGGER.debug("Dashboard client disconnected: %s", self.path)

            def log_message(self, format: str, *args: Any) -> None:
                LOGGER.debug("Dashboard HTTP: " + format, *args)

        self._httpd = ReusableThreadingHTTPServer((self._host, self._port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="dashboard-http",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info("Dashboard HTTP dimulai pada %s:%s", *self.address)

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._httpd = None
        self._thread = None
        LOGGER.info("Dashboard HTTP dihentikan")
