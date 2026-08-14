"""Concise terminal events and complete runtime logging for the USV server."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from server.config import Settings


TERMINAL_LOGGER_NAME = "usv.server_terminal"


class ServerTerminalReporter:
    """Emit meaningful server transitions without one line per telemetry record."""

    def __init__(self, *, summary_interval: int = 10) -> None:
        self._summary_interval = max(1, int(summary_interval))
        self._logger = logging.getLogger(TERMINAL_LOGGER_NAME)
        self._lock = threading.RLock()
        self._mqtt_connected: bool | None = None
        self._last_session_by_vehicle: dict[str, str] = {}
        self._last_state_by_vehicle: dict[str, str] = {}
        self._stored_since_summary: dict[str, int] = {}
        self._duplicate_since_summary: dict[str, int] = {}
        self._replay_since_summary: dict[str, int] = {}

    def event(self, category: str, message: str, *, level: str = "INFO") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        category_text = category.upper()[:10]
        line = f"{timestamp} [{level:<5}] [{category_text:<10}] {message}"
        if level == "ERROR":
            self._logger.error(line)
        elif level in {"WARN", "WARNING"}:
            self._logger.warning(line)
        else:
            self._logger.info(line)

    def mqtt_status(self, connected: bool, detail: str = "") -> None:
        with self._lock:
            if self._mqtt_connected is connected:
                return
            self._mqtt_connected = connected
        if connected:
            self.event("MQTT", f"Broker connected{_suffix(detail)}")
        else:
            self.event("MQTT", f"Broker disconnected{_suffix(detail)}", level="WARN")

    def telemetry_processed(
        self,
        *,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
        state: str,
        delivery_type: str,
        inserted: bool,
        duplicate: bool,
        ack_published: bool,
    ) -> None:
        with self._lock:
            previous_session = self._last_session_by_vehicle.get(vehicle_id)
            if previous_session != session_id:
                self._last_session_by_vehicle[vehicle_id] = session_id
                self._last_state_by_vehicle.pop(vehicle_id, None)
                self._stored_since_summary[vehicle_id] = 0
                self._duplicate_since_summary[vehicle_id] = 0
                self._replay_since_summary[vehicle_id] = 0
                self.event(
                    "SESSION",
                    f"vehicle={vehicle_id} session={session_id}",
                )

            previous_state = self._last_state_by_vehicle.get(vehicle_id)
            if previous_state != state:
                if previous_state is None:
                    message = f"vehicle={vehicle_id} state={state} seq={seq_id}"
                else:
                    message = (
                        f"vehicle={vehicle_id} {previous_state} -> {state} "
                        f"seq={seq_id}"
                    )
                self._last_state_by_vehicle[vehicle_id] = state
                self.event("STATE", message)

            if inserted:
                self._stored_since_summary[vehicle_id] = (
                    self._stored_since_summary.get(vehicle_id, 0) + 1
                )
            if duplicate:
                self._duplicate_since_summary[vehicle_id] = (
                    self._duplicate_since_summary.get(vehicle_id, 0) + 1
                )
            if delivery_type == "REPLAY":
                self._replay_since_summary[vehicle_id] = (
                    self._replay_since_summary.get(vehicle_id, 0) + 1
                )

            total = (
                self._stored_since_summary.get(vehicle_id, 0)
                + self._duplicate_since_summary.get(vehicle_id, 0)
            )
            if total < self._summary_interval:
                return

            stored = self._stored_since_summary.get(vehicle_id, 0)
            duplicates = self._duplicate_since_summary.get(vehicle_id, 0)
            replay = self._replay_since_summary.get(vehicle_id, 0)
            self._stored_since_summary[vehicle_id] = 0
            self._duplicate_since_summary[vehicle_id] = 0
            self._replay_since_summary[vehicle_id] = 0

        self.event(
            "TELEMETRY",
            (
                f"vehicle={vehicle_id} last_seq={seq_id} "
                f"stored={stored} duplicate={duplicates} replay={replay} "
                f"ack={'SENT' if ack_published else 'FAILED'}"
            ),
            level="INFO" if ack_published else "WARN",
        )

    def mission_processed(
        self,
        *,
        vehicle_id: str,
        session_id: str,
        mission_total: int,
        executable_total: int,
        inserted: bool,
        retained: bool,
    ) -> None:
        self.event(
            "MISSION",
            (
                f"vehicle={vehicle_id} session={session_id} "
                f"items={mission_total} executable={executable_total} "
                f"source={'RETAINED' if retained else 'LIVE'} "
                f"database={'STORED' if inserted else 'EXISTS'}"
            ),
        )


def configure_server_logging(settings: Settings) -> Path:
    """Write all technical detail to file and only curated events to terminal."""

    log_directory = settings.project_root / "data" / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    runtime_log_path = log_directory / "server_runtime.log"

    complete_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    file_handler = RotatingFileHandler(
        runtime_log_path,
        maxBytes=10_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(complete_formatter)
    root_logger.addHandler(file_handler)

    terminal_logger = logging.getLogger(TERMINAL_LOGGER_NAME)
    terminal_logger.setLevel(logging.INFO)
    terminal_logger.handlers.clear()
    terminal_logger.propagate = False
    terminal_handler = logging.StreamHandler()
    terminal_handler.setFormatter(logging.Formatter("%(message)s"))
    terminal_logger.addHandler(terminal_handler)

    return runtime_log_path


def print_server_banner(
    settings: Settings,
    runtime_log_path: Path,
    reporter: ServerTerminalReporter,
) -> None:
    reporter.event("SYSTEM", "USV SERVER AND DASHBOARD STARTING")
    reporter.event("CONFIG", f"Broker={settings.mqtt_host}:{settings.mqtt_port}")
    reporter.event("CONFIG", f"Database={settings.database_path}")
    reporter.event(
        "DASHBOARD",
        f"http://{settings.dashboard_host}:{settings.dashboard_port}",
    )
    reporter.event("LOG", f"Runtime={runtime_log_path}")


def _suffix(detail: str) -> str:
    cleaned = detail.strip()
    return f" ({cleaned})" if cleaned else ""
