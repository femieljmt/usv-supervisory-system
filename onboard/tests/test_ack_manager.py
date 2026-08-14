"""Tests for onboard application ACK processing."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from onboard.ack_manager import AckManager
from onboard.persistent_outbox import (
    OUTBOX_IN_FLIGHT,
    OUTBOX_PENDING,
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


def make_ack(
    seq_id: int,
    *,
    vehicle_id: str = "usv-01",
    session_id: str = "session-test",
    status: str = "ACKED",
) -> dict:
    return {
        "protocol_version": "1.0.0",
        "vehicle_id": vehicle_id,
        "session_id": session_id,
        "seq_id": seq_id,
        "status": status,
        "stored_at": "2026-07-10T01:00:01.000Z",
    }


class AckManagerTest(TestCase):

    def test_exact_ack_removes_record(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )
            outbox.enqueue(make_payload(1))

            manager = AckManager("usv-01", outbox)

            manager.register_publish(
                "usv-01",
                "session-test",
                1,
            )

            result = manager.process_message(
                make_ack(1)
            )

            self.assertTrue(result.valid)
            self.assertTrue(result.matched)
            self.assertTrue(result.removed)
            self.assertEqual(outbox.count(), 0)
            self.assertEqual(manager.pending_count(), 0)

    def test_wrong_sequence_does_not_remove_record(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )
            outbox.enqueue(make_payload(1))

            manager = AckManager("usv-01", outbox)

            result = manager.process_message(
                make_ack(99)
            )

            self.assertTrue(result.valid)
            self.assertFalse(result.matched)
            self.assertFalse(result.removed)
            self.assertEqual(outbox.count(), 1)

    def test_wrong_vehicle_is_invalid(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )
            outbox.enqueue(make_payload(1))

            manager = AckManager("usv-01", outbox)

            result = manager.process_message(
                make_ack(
                    1,
                    vehicle_id="usv-02",
                )
            )

            self.assertFalse(result.valid)
            self.assertEqual(outbox.count(), 1)

    def test_non_acked_status_is_invalid(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )
            outbox.enqueue(make_payload(1))

            manager = AckManager("usv-01", outbox)

            result = manager.process_message(
                make_ack(
                    1,
                    status="REJECTED",
                )
            )

            self.assertFalse(result.valid)
            self.assertEqual(outbox.count(), 1)

    def test_timeout_returns_record_to_pending(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )
            outbox.enqueue(make_payload(1))

            outbox.mark_attempt(
                "usv-01",
                "session-test",
                1,
                increment_retry=False,
            )

            self.assertEqual(
                outbox.get_oldest()[0].delivery_status,
                OUTBOX_IN_FLIGHT,
            )

            manager = AckManager("usv-01", outbox)

            manager.register_publish(
                "usv-01",
                "session-test",
                1,
            )

            changed = manager.mark_timeout(
                "usv-01",
                "session-test",
                1,
            )

            self.assertTrue(changed)
            self.assertEqual(
                outbox.get_oldest()[0].delivery_status,
                OUTBOX_PENDING,
            )
            self.assertEqual(outbox.count(), 1)
