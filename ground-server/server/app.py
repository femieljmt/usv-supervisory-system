"""Unified ground server: MQTT, database, ACK, mission, and dashboard."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from dashboard.app import DashboardServer
from dashboard.database_reader import DashboardDataReader
from dashboard.routes import DashboardRoutes
from server.config import load_settings
from server.database import TelemetryDatabase
from server.mission_database import MissionDatabase
from server.mqtt_backend import MQTTBackend
from server.terminal_logging import (
    ServerTerminalReporter,
    configure_server_logging,
    print_server_banner,
)


LOGGER = logging.getLogger(__name__)


def _configure_console() -> None:
    """Keep terminal output readable on Windows and Linux."""

    if os.name != "nt":
        return

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _register_shutdown_signals(handler: object) -> None:
    """Register signals supported by the current operating system."""

    supported = [signal.SIGINT, signal.SIGTERM]
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        supported.append(sigbreak)

    for signal_number in supported:
        try:
            signal.signal(signal_number, handler)
        except (OSError, RuntimeError, ValueError):
            LOGGER.debug("Signal %s tidak didukung", signal_number)


def main() -> None:
    _configure_console()
    settings = load_settings()
    runtime_log_path = configure_server_logging(settings)
    reporter = ServerTerminalReporter(
        summary_interval=settings.terminal_summary_interval
    )
    print_server_banner(settings, runtime_log_path, reporter)

    telemetry_database = TelemetryDatabase(settings.database_path)
    mission_database = MissionDatabase(settings.database_path)
    telemetry_database.initialize()
    mission_database.initialize()

    dashboard_reader = DashboardDataReader(
        telemetry_database,
        mission_database,
        live_seconds=settings.dashboard_live_seconds,
        stale_seconds=settings.dashboard_stale_seconds,
    )
    dashboard_routes = DashboardRoutes(
        project_root=settings.project_root,
        reader=dashboard_reader,
        default_track_limit=settings.dashboard_track_limit,
        refresh_seconds=settings.dashboard_refresh_seconds,
    )
    dashboard = DashboardServer(
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        routes=dashboard_routes,
    )
    backend = MQTTBackend(
        settings,
        telemetry_database,
        mission_database,
        reporter,
    )

    stop_event = threading.Event()

    def request_shutdown(signal_number: int, frame: object) -> None:
        LOGGER.info("Shutdown diminta melalui signal %s", signal_number)
        reporter.event("SYSTEM", f"Shutdown signal={signal_number}")
        stop_event.set()

    _register_shutdown_signals(request_shutdown)

    dashboard_started = False
    backend_started = False

    try:
        dashboard.start()
        dashboard_started = True
        host, port = dashboard.address
        reporter.event("DASHBOARD", f"Ready at http://{host}:{port}")

        backend.start()
        backend_started = True

        while not stop_event.wait(timeout=0.5):
            pass
    finally:
        if backend_started:
            backend.stop()
        if dashboard_started:
            dashboard.stop()
        reporter.event("SYSTEM", "Server stopped")
        LOGGER.info("USV server selesai")


if __name__ == "__main__":
    main()
