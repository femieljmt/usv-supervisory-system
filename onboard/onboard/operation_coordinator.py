"""Periodic production of complete USV supervisory operation records."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onboard.config import Settings
from onboard.internet_monitor import InternetMonitor
from onboard.local_logger import LocalOperationLogger
from onboard.mavlink_reader import MAVLinkReader
from onboard.models import TelemetryStore
from onboard.mqtt_manager import MQTTManager
from onboard.persistent_outbox import OutboxRecord, PersistentOutbox
from onboard.protocol import (
    InternetStatus,
    MQTTConnectionStatus,
    SupervisorState,
    SyncStatus,
)
from onboard.record_builder import OperationRecordBuilder, RecordBuildContext
from onboard.state_machine import (
    StateInputs,
    StateTransition,
    SupervisoryStateMachine,
)
from onboard.synchronizer import OutboxSynchronizer
from onboard.terminal_display import TERMINAL_LOGGER_NAME, format_terminal_record


LOGGER = logging.getLogger(__name__)
TERMINAL_LOGGER = logging.getLogger(TERMINAL_LOGGER_NAME)


class NoPixhawkDataError(RuntimeError):
    """Raised when a valid Pixhawk heartbeat is unavailable."""


@dataclass(frozen=True)
class ProductionResult:
    record: dict[str, Any]
    log_path: Path
    outbox_record: OutboxRecord
    state_transition: StateTransition


class OperationCoordinator:
    """Create, locally log, buffer, and display operation records."""

    def __init__(
        self,
        settings: Settings,
        telemetry_store: TelemetryStore,
        mavlink_reader: MAVLinkReader,
        internet_monitor: InternetMonitor,
        mqtt_manager: MQTTManager,
        state_machine: SupervisoryStateMachine,
        record_builder: OperationRecordBuilder,
        local_logger: LocalOperationLogger,
        outbox: PersistentOutbox,
        synchronizer: OutboxSynchronizer,
        terminal_events: object | None = None,
    ) -> None:
        self._settings = settings
        self._telemetry_store = telemetry_store
        self._mavlink_reader = mavlink_reader
        self._internet_monitor = internet_monitor
        self._mqtt_manager = mqtt_manager
        self._state_machine = state_machine
        self._record_builder = record_builder
        self._local_logger = local_logger
        self._outbox = outbox
        self._synchronizer = synchronizer
        self._terminal_events = terminal_events

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._last_result: ProductionResult | None = None
        self._last_error: str | None = None
        self._last_internet_status: InternetStatus | None = None
        self._last_mqtt_status: MQTTConnectionStatus | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_result(self) -> ProductionResult | None:
        with self._lock:
            return self._last_result

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="operation-coordinator",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        LOGGER.info("Operation coordinator dihentikan")

    def produce_once(self) -> ProductionResult:
        if not self._mavlink_reader.pixhawk_available:
            raise NoPixhawkDataError(
                "NO DATA: heartbeat Pixhawk tidak tersedia"
            )

        internet_status = self._internet_monitor.status
        mqtt_status = self._mqtt_connection_status()
        self._emit_communication_transitions(
            internet_status,
            mqtt_status,
        )

        transition = self._state_machine.evaluate(
            StateInputs(
                pixhawk_available=True,
                internet_status=internet_status,
                recovery_active=self._synchronizer.recovery_active,
            )
        )

        if transition.changed:
            self._emit_state_transition(transition)
            LOGGER.info(
                "Supervisor state berubah: %s -> %s; reason=%s",
                transition.previous_state.value,
                transition.current_state.value,
                transition.reason,
            )

        sync_status = (
            SyncStatus.SYNCING
            if transition.current_state == SupervisorState.RECOVERY
            else SyncStatus.IDLE
        )
        snapshot = self._telemetry_store.get_snapshot()
        backlog_before_enqueue = self._outbox.count()

        context = RecordBuildContext(
            supervisor_state=transition.current_state,
            internet_status=internet_status,
            mqtt_connection_status=mqtt_status,
            sync_status=sync_status,
            pixhawk_available=True,
            buffer_count_after_enqueue=backlog_before_enqueue + 1,
        )
        record = self._record_builder.build_live_record(snapshot, context)

        try:
            log_path = self._local_logger.write(record)
        except Exception:
            self._emit(
                "LOG",
                "Gagal mencatat operation CSV",
                level="ERROR",
            )
            raise

        outbox_record = self._outbox.enqueue(record)

        display_sync = self._display_sync_status(
            state=transition.current_state,
            internet_status=internet_status,
            mqtt_status=mqtt_status,
            backlog_count=backlog_before_enqueue,
        )
        TERMINAL_LOGGER.info(
            format_terminal_record(
                record,
                backlog_count=backlog_before_enqueue,
                log_status="OK",
                display_sync_status=display_sync,
            )
        )

        self._synchronizer.notify_new_record()
        result = ProductionResult(
            record=record,
            log_path=log_path,
            outbox_record=outbox_record,
            state_transition=transition,
        )
        with self._lock:
            self._last_result = result
            self._last_error = None

        LOGGER.info(
            "Record dibuat: state=%s seq=%s mqtt=%s internet=%s buffer=%s",
            record["supervisor_state"],
            record["seq_id"],
            record["mqtt_connection_status"],
            record["internet_status"],
            self._outbox.count(),
        )
        return result

    def _run(self) -> None:
        LOGGER.info(
            "Operation coordinator dimulai; interval=%.2fs",
            self._settings.log_interval_seconds,
        )
        next_run = time.monotonic()

        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_run and self._stop_event.wait(next_run - now):
                break

            try:
                self.produce_once()
            except NoPixhawkDataError as exc:
                with self._lock:
                    self._last_error = str(exc)
            except Exception as exc:
                LOGGER.exception("Gagal membentuk atau menyimpan record operasi")
                with self._lock:
                    self._last_error = str(exc)

            next_run += self._settings.log_interval_seconds
            current_time = time.monotonic()
            if next_run < current_time:
                next_run = current_time + self._settings.log_interval_seconds

        LOGGER.info("Operation coordinator worker selesai")

    def _display_sync_status(
        self,
        *,
        state: SupervisorState,
        internet_status: InternetStatus,
        mqtt_status: MQTTConnectionStatus,
        backlog_count: int,
    ) -> str:
        if internet_status == InternetStatus.UNAVAILABLE:
            return "PAUSED"
        if state == SupervisorState.RECOVERY:
            return "SYNCING"
        if mqtt_status != MQTTConnectionStatus.CONNECTED:
            return "WAITING" if backlog_count > 0 else "IDLE"
        if backlog_count > 0:
            return "WAITING"
        return "IDLE"

    def _emit_communication_transitions(
        self,
        internet_status: InternetStatus,
        mqtt_status: MQTTConnectionStatus,
    ) -> None:
        if internet_status != self._last_internet_status:
            if internet_status == InternetStatus.UNAVAILABLE:
                self._emit(
                    "NETWORK",
                    "Internet onboard terputus",
                    level="WARN",
                )
                self._emit(
                    "BUFFER",
                    "Local logging tetap berjalan; data disimpan di outbox",
                )
            elif internet_status == InternetStatus.AVAILABLE:
                message = (
                    "Internet onboard tersedia"
                    if self._last_internet_status is None
                    else "Internet onboard kembali tersedia"
                )
                self._emit("NETWORK", message)
            self._last_internet_status = internet_status

        if mqtt_status != self._last_mqtt_status:
            if mqtt_status == MQTTConnectionStatus.CONNECTED:
                self._emit("MQTT", "Broker MQTT terhubung")
            elif mqtt_status == MQTTConnectionStatus.DISCONNECTED:
                self._emit(
                    "MQTT",
                    "Broker MQTT terputus; state tidak berubah",
                    level="WARN",
                )
            self._last_mqtt_status = mqtt_status

    def _emit_state_transition(self, transition: StateTransition) -> None:
        manager = self._terminal_events
        if manager is not None and hasattr(manager, "state_transition"):
            manager.state_transition(
                transition.previous_state.value,
                transition.current_state.value,
            )
        else:
            self._emit(
                "STATE",
                f"{transition.previous_state.value} -> "
                f"{transition.current_state.value}",
            )

    def _emit(
        self,
        category: str,
        message: str,
        *,
        level: str = "INFO",
    ) -> None:
        manager = self._terminal_events
        if manager is not None and hasattr(manager, "emit"):
            manager.emit(category, message, level=level)

    def _mqtt_connection_status(self) -> MQTTConnectionStatus:
        raw_status = self._mqtt_manager.connection_status
        try:
            return MQTTConnectionStatus(raw_status)
        except ValueError:
            LOGGER.warning(
                "Status MQTT tidak dikenal: %r; digunakan DISCONNECTED",
                raw_status,
            )
            return MQTTConnectionStatus.DISCONNECTED
