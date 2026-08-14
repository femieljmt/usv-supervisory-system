"""Tests for the SQLite persistent outbox."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from onboard.persistent_outbox import (
    OUTBOX_IN_FLIGHT,
    OUTBOX_PENDING,
    DuplicateOutboxRecordError,
    PersistentOutbox,
)


def make_payload(seq_id: int) -> dict:
    return {
        "protocol_version": "1.0.0",
        "vehicle_id": "usv-01",
        "session_id": "session-test",
        "seq_id": seq_id,
        "timestamp": "2026-07-10T01:00:00.000Z",
        "data_source": "PIXHAWK_MAVLINK",
        "delivery_type": "LIVE",
        "supervisor_state": "NORMAL",
        "mission_status": "IDLE",
        "ap_link": "OK",
        "internet_status": "AVAILABLE",
        "mqtt_connection_status": "CONNECTED",
        "mqtt_publish_status": "PENDING",
        "ack_status": "PENDING",
        "ack_latency_ms": None,
        "retry_count": 0,
        "buffer_count": seq_id,
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


class PersistentOutboxTest(TestCase):

    def test_records_survive_restart_and_ack_is_exact(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory) / "outbox.sqlite3"
            )

            outbox_one = PersistentOutbox(database_path)
            outbox_one.initialize()

            outbox_one.enqueue(make_payload(1))
            outbox_one.enqueue(make_payload(2))
            outbox_one.enqueue(make_payload(3))

            self.assertEqual(outbox_one.count(), 3)

            outbox_two = PersistentOutbox(database_path)
            outbox_two.initialize()

            self.assertEqual(outbox_two.count(), 3)

            wrong_ack = outbox_two.acknowledge(
                "usv-01",
                "session-test",
                99,
            )
            self.assertFalse(wrong_ack)
            self.assertEqual(outbox_two.count(), 3)

            correct_ack = outbox_two.acknowledge(
                "usv-01",
                "session-test",
                2,
            )
            self.assertTrue(correct_ack)
            self.assertEqual(outbox_two.count(), 2)

            remaining = outbox_two.get_oldest(limit=10)

            self.assertEqual(
                [record.seq_id for record in remaining],
                [1, 3],
            )

    def test_duplicate_identity_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory) / "outbox.sqlite3"
            )

            outbox = PersistentOutbox(database_path)
            outbox.enqueue(make_payload(1))

            with self.assertRaises(
                DuplicateOutboxRecordError
            ):
                outbox.enqueue(make_payload(1))

    def test_in_flight_record_recovers_after_restart(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory) / "outbox.sqlite3"
            )

            outbox = PersistentOutbox(database_path)
            outbox.enqueue(make_payload(1))

            changed = outbox.mark_attempt(
                "usv-01",
                "session-test",
                1,
                increment_retry=False,
            )
            self.assertTrue(changed)

            record = outbox.get_oldest()[0]
            self.assertEqual(
                record.delivery_status,
                OUTBOX_IN_FLIGHT,
            )

            restarted_outbox = PersistentOutbox(database_path)
            recovered = (
                restarted_outbox.reset_in_flight_to_pending()
            )

            self.assertEqual(recovered, 1)

            record_after_restart = (
                restarted_outbox.get_oldest()[0]
            )
            self.assertEqual(
                record_after_restart.delivery_status,
                OUTBOX_PENDING,
            )
