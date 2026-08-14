"""Main entry point for Raspberry Pi USV supervisory application.

Runtime policy:

1. Menunggu heartbeat Pixhawk.
2. Tidak membuat operation record sebelum Pixhawk tersedia.
3. Mengunduh mission plan setelah Pixhawk terhubung.
4. Menampilkan dan mencatat daftar waypoint.
5. Menjalankan supervisory operation logging.
6. Menghentikan supervisory ketika heartbeat Pixhawk hilang.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from logging.handlers import RotatingFileHandler

from onboard.ack_manager import AckManager
from onboard.config import Settings, load_settings
from onboard.internet_monitor import InternetMonitor
from onboard.local_logger import LocalOperationLogger
from onboard.mavlink_reader import MAVLinkReader
from onboard.mission_plan import (
    MissionPlanLogger,
    MissionPlanStore,
)
from onboard.mission_plan_coordinator import (
    MissionPlanCoordinator,
)
from onboard.models import TelemetryStore
from onboard.mqtt_manager import MQTTManager
from onboard.operation_coordinator import (
    OperationCoordinator,
)
from onboard.persistent_outbox import PersistentOutbox
from onboard.protocol import InternetStatus
from onboard.record_builder import OperationRecordBuilder
from onboard.sequence_manager import SequenceManager
from onboard.session_storage import SessionStorage
from onboard.state_machine import SupervisoryStateMachine
from onboard.synchronizer import OutboxSynchronizer
from onboard.terminal_display import (
    TERMINAL_LOGGER_NAME,
    terminal_banner,
)
from onboard.terminal_events import TerminalEventManager


LOGGER = logging.getLogger(__name__)


MISSION_WAIT_TIMEOUT_SECONDS = 10.0


def configure_logging(
    settings: Settings,
    session_runtime_path,
) -> None:
    """Configure terminal, aggregate, and per-session runtime logging."""

    settings.local_log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    session_runtime_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    aggregate_runtime_path = (
        settings.local_log_dir
        / "supervisory_runtime.log"
    )

    complete_formatter = logging.Formatter(
        "%(asctime)s "
        "[%(levelname)s] "
        "%(name)s: %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    for runtime_path, backup_count in (
        (aggregate_runtime_path, 5),
        (session_runtime_path, 2),
    ):
        file_handler = RotatingFileHandler(
            runtime_path,
            maxBytes=5_000_000,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            complete_formatter
        )
        root_logger.addHandler(
            file_handler
        )

    # Detail internal disimpan ke dua file: aggregate dan session.
    # Terminal hanya menampilkan banner, event penting, mission plan,
    # dan satu baris operasi per interval.
    terminal_logger = logging.getLogger(
        TERMINAL_LOGGER_NAME
    )
    terminal_logger.setLevel(logging.INFO)
    terminal_logger.handlers.clear()
    terminal_logger.propagate = False

    terminal_handler = logging.StreamHandler()
    terminal_handler.setLevel(logging.INFO)
    terminal_handler.setFormatter(
        logging.Formatter("%(message)s")
    )

    terminal_logger.addHandler(
        terminal_handler
    )


def drain_outbox_before_shutdown(
    *,
    outbox: PersistentOutbox,
    synchronizer: OutboxSynchronizer,
    timeout_seconds: float,
    terminal_events: object | None = None,
    poll_interval_seconds: float = 0.2,
) -> tuple[bool, float, int]:
    """Drain already-produced records without creating new telemetry records.

    The operation coordinator must be stopped before this helper is called.
    MQTT, internet monitoring, and the synchronizer remain active so pending
    records can receive exact application ACKs. The wait is bounded; records
    that cannot be delivered remain safely stored in the persistent outbox.
    """

    timeout = max(0.0, float(timeout_seconds))
    started = time.monotonic()
    initial_pending = outbox.count()

    def emit(message: str, *, level: str = "INFO") -> None:
        if terminal_events is not None and hasattr(terminal_events, "emit"):
            terminal_events.emit("SHUTDOWN", message, level=level)

    if initial_pending <= 0 and synchronizer.in_flight_count <= 0:
        emit("Tidak ada record pending; session dapat ditutup")
        return True, 0.0, 0

    emit(
        f"Produksi record dihentikan; menunggu ACK untuk {initial_pending} record"
    )
    LOGGER.info(
        "Graceful shutdown drain dimulai; pending=%s timeout=%.1fs",
        initial_pending,
        timeout,
    )
    synchronizer.notify_new_record()
    last_reported = initial_pending

    while True:
        pending = outbox.count()
        in_flight = synchronizer.in_flight_count
        elapsed = time.monotonic() - started

        if pending <= 0 and in_flight <= 0:
            emit(f"Seluruh record telah di-ACK dalam {elapsed:.1f} detik")
            LOGGER.info("Graceful shutdown drain selesai; pending=0")
            return True, elapsed, 0

        if elapsed >= timeout:
            emit(
                f"Batas waktu tercapai; {pending} record tetap aman di outbox",
                level="WARN",
            )
            LOGGER.warning(
                "Graceful shutdown drain timeout; pending=%s in_flight=%s",
                pending,
                in_flight,
            )
            return False, elapsed, pending

        if pending != last_reported and (pending == 0 or abs(pending - last_reported) >= 10):
            emit(f"Pending outbox: {pending} record")
            last_reported = pending

        synchronizer.notify_new_record()
        time.sleep(max(0.05, poll_interval_seconds))


def main() -> None:
    settings = load_settings()

    telemetry_store = TelemetryStore()
    mission_store = MissionPlanStore()

    # Persistent outbox tetap satu database lintas-session agar data yang
    # belum memperoleh ACK tidak hilang ketika aplikasi dimulai ulang.
    outbox = PersistentOutbox(
        settings.outbox_db_path
    )
    outbox.initialize()

    recovered_records = (
        outbox.reset_in_flight_to_pending()
    )

    sequence_manager = SequenceManager(
        settings.outbox_db_path,
        settings.vehicle_id,
    )
    sequence_manager.initialize()
    sequence_state = (
        sequence_manager.start_new_session()
    )

    sessions_root = (
        settings.local_log_dir.parent
        / "sessions"
    )
    session_storage = SessionStorage(
        sessions_root,
        sequence_state.session_id,
    )
    session_paths = session_storage.create(
        vehicle_id=settings.vehicle_id,
        outbox_path=settings.outbox_db_path,
        batch_size=settings.sync_batch_size,
        mqtt_host=settings.mqtt_host,
        mqtt_port=settings.mqtt_port,
        mavlink_connection=settings.mavlink_connection,
        initial_pending_outbox=outbox.count(),
    )

    configure_logging(
        settings,
        session_paths.runtime_file,
    )

    terminal_logger = logging.getLogger(
        TERMINAL_LOGGER_NAME
    )

    LOGGER.info(
        "USV supervisory application mulai"
    )
    LOGGER.info(
        "Vehicle ID: %s",
        settings.vehicle_id,
    )
    LOGGER.info(
        "Session ID: %s",
        sequence_state.session_id,
    )
    LOGGER.info(
        "Session directory: %s",
        session_paths.directory,
    )
    LOGGER.info(
        "MAVLink input: %s",
        settings.mavlink_connection,
    )
    LOGGER.info(
        "MQTT broker: %s:%s",
        settings.mqtt_host,
        settings.mqtt_port,
    )

    if recovered_records:
        LOGGER.info(
            "%s record IN_FLIGHT dipulihkan menjadi PENDING",
            recovered_records,
        )

    LOGGER.info(
        "Session startup: %s; seq_id dimulai dari 1; pending_outbox=%s",
        sequence_state.session_id,
        outbox.count(),
    )

    terminal_events = TerminalEventManager(
        logger=terminal_logger
    )

    terminal_logger.info(
        terminal_banner(
            settings.vehicle_id,
            settings.mqtt_host,
            settings.mqtt_port,
            session_id=sequence_state.session_id,
            batch_size=settings.sync_batch_size,
            session_directory=session_paths.directory,
            operation_file=session_paths.operation_file,
            runtime_file=session_paths.runtime_file,
            mission_file=session_paths.mission_file,
            session_info_file=session_paths.info_file,
            outbox_path=settings.outbox_db_path,
            pending_outbox=outbox.count(),
        )
    )

    local_logger = LocalOperationLogger(
        file_path=session_paths.operation_file,
        durable_write=True,
    )

    mission_plan_logger = MissionPlanLogger(
        file_path=session_paths.mission_file,
        history_path=(
            settings.local_log_dir
            / "mission_plan_history.jsonl"
        ),
    )

    mavlink_reader = MAVLinkReader(
        settings,
        telemetry_store,
        mission_store,
    )

    internet_monitor = InternetMonitor(
        settings
    )

    ack_manager = AckManager(
        settings.vehicle_id,
        outbox,
    )

    mqtt_manager = MQTTManager(
        settings,
        ack_manager,
    )

    mission_plan_coordinator = (
        MissionPlanCoordinator(
            vehicle_id=settings.vehicle_id,
            session_id=(
                sequence_state.session_id
            ),
            store=mission_store,
            logger=mission_plan_logger,
            mqtt_manager=mqtt_manager,
        )
    )

    state_machine = SupervisoryStateMachine()

    record_builder = OperationRecordBuilder(
        sequence_manager
    )

    def delivery_enabled() -> bool:
        return (
            internet_monitor.status
            != InternetStatus.UNAVAILABLE
        )

    synchronizer = OutboxSynchronizer(
        settings,
        outbox,
        mqtt_manager,
        ack_manager,
        delivery_enabled_provider=(
            delivery_enabled
        ),
        terminal_events=terminal_events,
    )

    coordinator = OperationCoordinator(
        settings=settings,
        telemetry_store=telemetry_store,
        mavlink_reader=mavlink_reader,
        internet_monitor=internet_monitor,
        mqtt_manager=mqtt_manager,
        state_machine=state_machine,
        record_builder=record_builder,
        local_logger=local_logger,
        outbox=outbox,
        synchronizer=synchronizer,
        terminal_events=terminal_events,
    )

    stop_event = threading.Event()
    shutdown_context = {
        "status": "CLOSED",
        "reason": "normal_shutdown",
        "signal": None,
        "drain_completed": False,
        "drain_elapsed_seconds": 0.0,
    }

    def request_shutdown(
        signal_number: int,
        frame: object,
    ) -> None:
        LOGGER.info(
            "Shutdown diminta melalui signal %s",
            signal_number,
        )
        shutdown_context["signal"] = signal_number
        shutdown_context["reason"] = (
            "operator_stop" if signal_number == signal.SIGINT else f"signal_{signal_number}"
        )
        stop_event.set()

    signal.signal(
        signal.SIGINT,
        request_shutdown,
    )
    signal.signal(
        signal.SIGTERM,
        request_shutdown,
    )

    supervisory_started = False
    mission_coordinator_started = False
    stopped_because_no_data = False

    try:
        mavlink_reader.start()

        terminal_events.emit(
            "PIXHAWK",
            "Menunggu heartbeat Pixhawk; NO DATA",
            level="WARN",
        )

        while not stop_event.wait(
            timeout=0.25
        ):
            if mavlink_reader.pixhawk_available:
                break

        if stop_event.is_set():
            return

        terminal_events.emit(
            "PIXHAWK",
            "Heartbeat diterima; Pixhawk terhubung",
        )
        terminal_events.emit(
            "MISSION",
            "Mengunduh mission plan dari Pixhawk",
        )

        LOGGER.info(
            "Heartbeat Pixhawk diterima"
        )

        internet_monitor.start()
        mqtt_manager.start()

        mission_plan_coordinator.start()
        mission_coordinator_started = True

        mission_deadline = (
            time.monotonic()
            + MISSION_WAIT_TIMEOUT_SECONDS
        )

        while (
            not stop_event.is_set()
            and time.monotonic()
            < mission_deadline
        ):
            mission_snapshot = (
                mission_store.get_snapshot()
            )

            if (
                mission_snapshot.complete
                or mission_snapshot.error
            ):
                break

            if not mavlink_reader.pixhawk_available:
                stopped_because_no_data = True
                break

            time.sleep(0.25)

        mission_snapshot = (
            mission_store.get_snapshot()
        )

        if mission_snapshot.complete:
            LOGGER.info(
                "Mission plan tersedia: "
                "total=%s executable=%s",
                mission_snapshot.total_count,
                mission_snapshot.executable_count,
            )

        elif mission_snapshot.error:
            terminal_logger.info(
                "MISSION PLAN : DOWNLOAD FAILED"
            )
            terminal_logger.info(
                f"ERROR        : {mission_snapshot.error}"
            )
            terminal_logger.info("")

        else:
            terminal_logger.info(
                "MISSION PLAN : NOT RECEIVED WITHIN 10 SECONDS"
            )
            terminal_logger.info(
                "ACTION       : Operation monitoring continues"
            )
            terminal_logger.info("")

        if stopped_because_no_data:
            shutdown_context["status"] = "NO_DATA"
            shutdown_context["reason"] = "pixhawk_heartbeat_lost"
            return

        synchronizer.start()
        coordinator.start()

        supervisory_started = True

        while not stop_event.wait(
            timeout=0.25
        ):
            if not mavlink_reader.pixhawk_available:
                stopped_because_no_data = True
                shutdown_context["status"] = "NO_DATA"
                shutdown_context["reason"] = "pixhawk_heartbeat_lost"

                LOGGER.warning(
                    "Heartbeat Pixhawk tidak diterima selama "
                    "%.1f detik; supervisory dihentikan",
                    settings.mavlink_heartbeat_timeout,
                )

                terminal_events.emit(
                    "PIXHAWK",
                    "Heartbeat hilang; NO DATA",
                    level="ERROR",
                )
                terminal_events.emit(
                    "SYSTEM",
                    "Supervisory dihentikan",
                    level="ERROR",
                )

                break

    finally:
        # Stop record production first. Delivery services remain alive during
        # the bounded drain so seq_id no longer grows while pending data is
        # acknowledged by the server.
        if supervisory_started:
            coordinator.stop()
            drain_completed, drain_elapsed, _ = drain_outbox_before_shutdown(
                outbox=outbox,
                synchronizer=synchronizer,
                timeout_seconds=settings.shutdown_drain_timeout_seconds,
                terminal_events=terminal_events,
            )
            shutdown_context["drain_completed"] = drain_completed
            shutdown_context["drain_elapsed_seconds"] = round(drain_elapsed, 3)
            synchronizer.stop()

        if mission_coordinator_started:
            mission_plan_coordinator.stop()

        mqtt_manager.stop()
        internet_monitor.stop()
        mavlink_reader.stop()

        final_pending = outbox.count()
        if stopped_because_no_data:
            shutdown_context["status"] = "NO_DATA"
        elif final_pending > 0:
            shutdown_context["status"] = "CLOSED_WITH_PENDING"
        else:
            shutdown_context["status"] = "CLOSED"

        LOGGER.info(
            "USV supervisory application selesai; "
            "sisa_outbox=%s drain_completed=%s",
            final_pending,
            shutdown_context["drain_completed"],
        )

        try:
            session_storage.update(
                {
                    "shutdown_signal": shutdown_context["signal"],
                    "shutdown_drain_timeout_seconds": (
                        settings.shutdown_drain_timeout_seconds
                    ),
                    "shutdown_drain_completed": shutdown_context["drain_completed"],
                    "shutdown_drain_elapsed_seconds": (
                        shutdown_context["drain_elapsed_seconds"]
                    ),
                }
            )
            session_storage.finalize(
                final_pending_outbox=final_pending,
                exit_reason=shutdown_context["reason"],
                status=shutdown_context["status"],
            )
        except Exception:
            LOGGER.exception(
                "Gagal memperbarui session_info.json saat shutdown"
            )

    if stopped_because_no_data:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
