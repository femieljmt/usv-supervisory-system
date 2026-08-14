"""Tests for onboard operation-record coordination."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest import TestCase

from onboard.internet_monitor import InternetSnapshot
from onboard.local_logger import LocalOperationLogger
from onboard.models import (
    TelemetrySnapshot,
    TelemetryStore,
)
from onboard.operation_coordinator import (
    NoPixhawkDataError,
    OperationCoordinator,
)
from onboard.persistent_outbox import (
    PersistentOutbox,
)
from onboard.protocol import (
    InternetStatus,
    MQTTConnectionStatus,
)
from onboard.record_builder import (
    OperationRecordBuilder,
)
from onboard.sequence_manager import (
    SequenceManager,
)
from onboard.state_machine import (
    SupervisoryStateMachine,
)


class FakeMAVLinkReader:
    def __init__(
        self,
        *,
        pixhawk_available: bool,
    ) -> None:
        self.pixhawk_available = (
            pixhawk_available
        )


class FakeInternetMonitor:
    def __init__(
        self,
        status: InternetStatus,
    ) -> None:
        self.status = status

    def get_snapshot(self) -> InternetSnapshot:
        return InternetSnapshot(
            status=self.status,
            consecutive_successes=0,
            consecutive_failures=0,
            checked_at=None,
            successful_target=None,
        )


class FakeMQTTManager:
    def __init__(
        self,
        status: MQTTConnectionStatus,
    ) -> None:
        self.connection_status = status.value

    @property
    def is_connected(self) -> bool:
        return (
            self.connection_status
            == MQTTConnectionStatus.CONNECTED.value
        )


class FakeSynchronizer:
    def __init__(
        self,
        *,
        recovery_active: bool,
    ) -> None:
        self.recovery_active = recovery_active
        self.last_action = (
            "PUBLISHED_REPLAY"
            if recovery_active
            else "IDLE"
        )
        self.notify_count = 0

    def notify_new_record(self) -> None:
        self.notify_count += 1


def make_settings():
    return SimpleNamespace(
        log_interval_seconds=1.0,
    )


class OperationCoordinatorTest(TestCase):

    def _build_components(
        self,
        temporary_directory: str,
        *,
        pixhawk_available: bool,
        internet_status: InternetStatus,
        mqtt_status: MQTTConnectionStatus,
        recovery_active: bool,
    ):
        root = Path(temporary_directory)

        database_path = (
            root / "outbox.sqlite3"
        )

        outbox = PersistentOutbox(
            database_path
        )
        outbox.initialize()

        telemetry_store = TelemetryStore()

        sequence_manager = SequenceManager(
            database_path,
            "usv-01",
        )

        record_builder = OperationRecordBuilder(
            sequence_manager,
            clock=lambda: (
                "2026-07-10T01:00:00.000Z"
            ),
        )

        local_logger = LocalOperationLogger(
            root / "logs",
            durable_write=False,
        )

        synchronizer = FakeSynchronizer(
            recovery_active=recovery_active,
        )

        coordinator = OperationCoordinator(
            settings=make_settings(),
            telemetry_store=telemetry_store,
            mavlink_reader=FakeMAVLinkReader(
                pixhawk_available=(
                    pixhawk_available
                )
            ),
            internet_monitor=FakeInternetMonitor(
                internet_status
            ),
            mqtt_manager=FakeMQTTManager(
                mqtt_status
            ),
            state_machine=(
                SupervisoryStateMachine()
            ),
            record_builder=record_builder,
            local_logger=local_logger,
            outbox=outbox,
            synchronizer=synchronizer,
        )

        return (
            coordinator,
            telemetry_store,
            outbox,
            synchronizer,
        )

    def test_pixhawk_missing_produces_no_data(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            (
                coordinator,
                telemetry_store,
                outbox,
                synchronizer,
            ) = self._build_components(
                temporary_directory,
                pixhawk_available=False,
                internet_status=(
                    InternetStatus.AVAILABLE
                ),
                mqtt_status=(
                    MQTTConnectionStatus.CONNECTED
                ),
                recovery_active=False,
            )

            with self.assertRaises(
                NoPixhawkDataError
            ):
                coordinator.produce_once()

            self.assertEqual(
                outbox.count(),
                0,
            )

            self.assertEqual(
                synchronizer.notify_count,
                0,
            )

            log_files = list(
                (
                    Path(temporary_directory)
                    / "logs"
                ).glob("operation_*.csv")
            )

            self.assertEqual(
                log_files,
                [],
            )

    def test_internet_loss_creates_gcs_lost_record(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            (
                coordinator,
                telemetry_store,
                outbox,
                synchronizer,
            ) = self._build_components(
                temporary_directory,
                pixhawk_available=True,
                internet_status=(
                    InternetStatus.UNAVAILABLE
                ),
                mqtt_status=(
                    MQTTConnectionStatus.DISCONNECTED
                ),
                recovery_active=False,
            )

            result = coordinator.produce_once()

            self.assertEqual(
                result.record["supervisor_state"],
                "GCS_LOST",
            )
            self.assertEqual(
                result.record["ap_link"],
                "OK",
            )
            self.assertEqual(
                result.record["internet_status"],
                "UNAVAILABLE",
            )
            self.assertEqual(outbox.count(), 1)

    def test_mqtt_down_with_internet_available_remains_normal(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            (
                coordinator,
                telemetry_store,
                outbox,
                synchronizer,
            ) = self._build_components(
                temporary_directory,
                pixhawk_available=True,
                internet_status=(
                    InternetStatus.AVAILABLE
                ),
                mqtt_status=(
                    MQTTConnectionStatus.DISCONNECTED
                ),
                recovery_active=False,
            )

            result = coordinator.produce_once()

            self.assertEqual(
                result.record["supervisor_state"],
                "NORMAL",
            )
            self.assertEqual(
                result.record["mqtt_connection_status"],
                "DISCONNECTED",
            )
            self.assertEqual(outbox.count(), 1)

    def test_active_sync_creates_recovery_record(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            (
                coordinator,
                telemetry_store,
                outbox,
                synchronizer,
            ) = self._build_components(
                temporary_directory,
                pixhawk_available=True,
                internet_status=(
                    InternetStatus.AVAILABLE
                ),
                mqtt_status=(
                    MQTTConnectionStatus.CONNECTED
                ),
                recovery_active=True,
            )

            result = coordinator.produce_once()

            self.assertEqual(
                result.record["supervisor_state"],
                "RECOVERY",
            )
            self.assertEqual(
                result.record["sync_status"],
                "SYNCING",
            )

    def test_normal_record_contains_mavlink_snapshot(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            (
                coordinator,
                telemetry_store,
                outbox,
                synchronizer,
            ) = self._build_components(
                temporary_directory,
                pixhawk_available=True,
                internet_status=(
                    InternetStatus.AVAILABLE
                ),
                mqtt_status=(
                    MQTTConnectionStatus.CONNECTED
                ),
                recovery_active=False,
            )

            telemetry_store.update(
                armed=True,
                flight_mode="AUTO",
                lat=2.1234567,
                lon=99.1234567,
                battery_v=15.8,
                gps_fix=3,
                gps_fix_label="3D_FIX",
            )

            result = coordinator.produce_once()

            self.assertEqual(
                result.record["supervisor_state"],
                "NORMAL",
            )
            self.assertEqual(
                result.record["mission_status"],
                "RUNNING",
            )
            self.assertEqual(
                result.record["lat"],
                2.1234567,
            )
            self.assertEqual(
                result.record["battery_v"],
                15.8,
            )
            self.assertEqual(outbox.count(), 1)

    def test_sequence_and_local_log_continue(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            (
                coordinator,
                telemetry_store,
                outbox,
                synchronizer,
            ) = self._build_components(
                temporary_directory,
                pixhawk_available=True,
                internet_status=(
                    InternetStatus.AVAILABLE
                ),
                mqtt_status=(
                    MQTTConnectionStatus.CONNECTED
                ),
                recovery_active=False,
            )

            first = coordinator.produce_once()
            second = coordinator.produce_once()

            self.assertEqual(
                first.record["seq_id"],
                1,
            )
            self.assertEqual(
                second.record["seq_id"],
                2,
            )
            self.assertEqual(
                first.record["session_id"],
                second.record["session_id"],
            )
            self.assertEqual(outbox.count(), 2)

            lines = (
                first.log_path
                .read_text(encoding="utf-8")
                .splitlines()
            )

            # Satu header dan dua record.
            self.assertEqual(len(lines), 3)
