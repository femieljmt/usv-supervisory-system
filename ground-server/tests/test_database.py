"""Tests for Raspberry Pi Lab telemetry database."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from server.database import TelemetryDatabase


def make_payload(
    seq_id: int,
    *,
    session_id: str = "session-test",
    delivery_type: str = "LIVE",
) -> dict:
    return {
        "protocol_version": "1.0.0",
        "vehicle_id": "usv-01",
        "session_id": session_id,
        "seq_id": seq_id,
        "timestamp": "2026-07-10T01:00:00.000Z",
        "data_source": "PIXHAWK_MAVLINK",
        "delivery_type": delivery_type,
        "supervisor_state": "NORMAL",
        "mission_status": "IDLE",
        "ap_link": "OK",
        "internet_status": "AVAILABLE",
        "mqtt_connection_status": "CONNECTED",
        "mqtt_publish_status": "PENDING",
        "ack_status": "PENDING",
        "ack_latency_ms": None,
        "retry_count": 0,
        "buffer_count": 1,
        "sync_status": "IDLE",
        "armed": False,
        "flight_mode": "HOLD",
        "lat": None,
        "lon": None,
        "heading": None,
        "groundspeed": None,
        "battery_v": None,
        "battery_remaining_pct": None,
        "gps_fix": None,
        "gps_fix_label": "UNKNOWN",
        "gps_hdop": None,
        "wp_index": 0,
        "wp_total": 0,
        "wp_dist": None,
        "mission_loaded": False,
        "mission_complete": False,
    }


class TelemetryDatabaseTest(TestCase):

    def test_record_survives_database_reopen(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory)
                / "server.sqlite3"
            )

            database_one = TelemetryDatabase(
                database_path
            )
            database_one.initialize()

            result = database_one.store_telemetry(
                make_payload(1),
                source_topic="usv/usv-01/telemetry",
            )

            self.assertTrue(result.inserted)
            self.assertFalse(result.duplicate)
            self.assertEqual(
                database_one.count_records(),
                1,
            )

            database_two = TelemetryDatabase(
                database_path
            )

            self.assertEqual(
                database_two.count_records(),
                1,
            )

            record = database_two.get_record(
                "usv-01",
                "session-test",
                1,
            )

            self.assertIsNotNone(record)
            self.assertEqual(
                record["supervisor_state"],
                "NORMAL",
            )
            self.assertEqual(
                record["receive_count"],
                1,
            )

    def test_ack_information_is_recorded(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory)
                / "server.sqlite3"
            )

            database = TelemetryDatabase(database_path)

            database.store_telemetry(make_payload(1))

            updated = database.mark_ack_sent(
                "usv-01",
                "session-test",
                1,
            )

            self.assertTrue(updated)

            record = database.get_record(
                "usv-01",
                "session-test",
                1,
            )

            self.assertIsNotNone(
                record["backend_ack_sent_at"]
            )
            self.assertEqual(
                record["ack_send_count"],
                1,
            )

    def test_wrong_identity_does_not_update_ack(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory)
                / "server.sqlite3"
            )

            database = TelemetryDatabase(database_path)
            database.store_telemetry(make_payload(1))

            updated = database.mark_ack_sent(
                "usv-01",
                "session-test",
                99,
            )

            self.assertFalse(updated)
