"""Tests for operation record construction."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from onboard.models import TelemetrySnapshot
from onboard.protocol import (
    InternetStatus,
    MQTTConnectionStatus,
    SupervisorState,
    SyncStatus,
    validate_telemetry_payload,
)
from onboard.record_builder import (
    MISSION_COMPLETED,
    MISSION_IDLE,
    MISSION_RUNNING,
    OperationRecordBuilder,
    RecordBuildContext,
    determine_mission_status,
)
from onboard.sequence_manager import SequenceManager


FIXED_TIMESTAMP = "2026-07-10T01:00:00.000Z"


def make_context(
    *,
    pixhawk_available: bool = True,
    buffer_count: int = 1,
) -> RecordBuildContext:
    return RecordBuildContext(
        supervisor_state=SupervisorState.NORMAL,
        internet_status=InternetStatus.AVAILABLE,
        mqtt_connection_status=(
            MQTTConnectionStatus.CONNECTED
        ),
        sync_status=SyncStatus.IDLE,
        pixhawk_available=pixhawk_available,
        buffer_count_after_enqueue=buffer_count,
    )


class OperationRecordBuilderTest(TestCase):

    def test_builds_complete_valid_record(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            sequence_manager = SequenceManager(
                database_path,
                "usv-01",
            )

            builder = OperationRecordBuilder(
                sequence_manager,
                clock=lambda: FIXED_TIMESTAMP,
            )

            snapshot = TelemetrySnapshot(
                armed=True,
                flight_mode="AUTO",
                lat=2.1234567,
                lon=99.1234567,
                heading=90.0,
                groundspeed=1.2,
                battery_v=15.8,
                battery_remaining_pct=80,
                gps_fix=3,
                gps_fix_label="3D_FIX",
                gps_hdop=0.9,
                wp_index=1,
                wp_total=5,
                wp_dist=12.5,
                mission_loaded=True,
                mission_complete=False,
            )

            record = builder.build_live_record(
                snapshot,
                make_context(),
            )

            validate_telemetry_payload(record)

            self.assertEqual(
                record["protocol_version"],
                "1.0.0",
            )
            self.assertEqual(
                record["vehicle_id"],
                "usv-01",
            )
            self.assertEqual(record["seq_id"], 1)
            self.assertEqual(
                record["timestamp"],
                FIXED_TIMESTAMP,
            )
            self.assertEqual(
                record["delivery_type"],
                "LIVE",
            )
            self.assertEqual(
                record["supervisor_state"],
                "NORMAL",
            )
            self.assertEqual(
                record["mission_status"],
                "RUNNING",
            )
            self.assertEqual(
                record["ap_link"],
                "OK",
            )
            self.assertEqual(
                record["mqtt_publish_status"],
                "PENDING",
            )
            self.assertEqual(
                record["ack_status"],
                "PENDING",
            )
            self.assertEqual(
                record["buffer_count"],
                1,
            )
            self.assertEqual(
                record["lat"],
                2.1234567,
            )

    def test_sequence_continues_after_reopen(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            first_builder = OperationRecordBuilder(
                SequenceManager(
                    database_path,
                    "usv-01",
                ),
                clock=lambda: FIXED_TIMESTAMP,
            )

            first = first_builder.build_live_record(
                TelemetrySnapshot(),
                make_context(),
            )

            second_builder = OperationRecordBuilder(
                SequenceManager(
                    database_path,
                    "usv-01",
                ),
                clock=lambda: FIXED_TIMESTAMP,
            )

            second = second_builder.build_live_record(
                TelemetrySnapshot(),
                make_context(),
            )

            self.assertEqual(first["seq_id"], 1)
            self.assertEqual(second["seq_id"], 2)
            self.assertEqual(
                first["session_id"],
                second["session_id"],
            )

    def test_pixhawk_unavailable_sets_ap_link_lost(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            builder = OperationRecordBuilder(
                SequenceManager(
                    Path(temporary_directory)
                    / "outbox.sqlite3",
                    "usv-01",
                ),
                clock=lambda: FIXED_TIMESTAMP,
            )

            record = builder.build_live_record(
                TelemetrySnapshot(),
                make_context(
                    pixhawk_available=False
                ),
            )

            self.assertEqual(
                record["ap_link"],
                "LOST",
            )

            # Nilai MAVLink yang belum diterima tetap None.
            self.assertIsNone(record["lat"])
            self.assertIsNone(record["lon"])
            self.assertIsNone(
                record["battery_v"]
            )

    def test_invalid_buffer_count_is_rejected(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            builder = OperationRecordBuilder(
                SequenceManager(
                    Path(temporary_directory)
                    / "outbox.sqlite3",
                    "usv-01",
                )
            )

            with self.assertRaises(ValueError):
                builder.build_live_record(
                    TelemetrySnapshot(),
                    make_context(buffer_count=-1),
                )

    def test_mission_status_rules(self) -> None:
        self.assertEqual(
            determine_mission_status(
                TelemetrySnapshot()
            ),
            MISSION_IDLE,
        )

        self.assertEqual(
            determine_mission_status(
                TelemetrySnapshot(
                    armed=True,
                    flight_mode="AUTO",
                )
            ),
            MISSION_RUNNING,
        )

        self.assertEqual(
            determine_mission_status(
                TelemetrySnapshot(
                    mission_complete=True,
                )
            ),
            MISSION_COMPLETED,
        )
