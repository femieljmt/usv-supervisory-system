"""Acceptance tests for the locked final recovery concept."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from paho.mqtt import client as mqtt

from onboard.ack_manager import AckManager
from onboard.mqtt_manager import PublishResult
from onboard.persistent_outbox import (
    OUTBOX_IN_FLIGHT,
    OUTBOX_PENDING,
    PersistentOutbox,
)
from onboard.synchronizer import OutboxSynchronizer
from tests.test_ack_manager import make_ack, make_payload


class FakeMQTTManager:
    def __init__(self) -> None:
        self.is_connected = True
        self.published_payloads: list[dict] = []

    def publish_telemetry(self, payload: dict) -> PublishResult:
        self.published_payloads.append(dict(payload))
        return PublishResult(
            accepted=True,
            topic=f"usv/{payload['vehicle_id']}/telemetry",
            rc=mqtt.MQTT_ERR_SUCCESS,
            message_id=len(self.published_payloads),
        )


class CapturingEvents:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def emit(
        self,
        category: str,
        message: str,
        *,
        level: str = "INFO",
    ) -> None:
        self.events.append((level, category, message))


class FinalRecoveryBatchTest(TestCase):

    def test_23_gcs_lost_records_use_batches_10_10_3(self) -> None:
        with TemporaryDirectory() as directory:
            outbox = PersistentOutbox(Path(directory) / "outbox.sqlite3")
            for seq_id in range(1, 24):
                payload = make_payload(seq_id)
                payload["supervisor_state"] = "GCS_LOST"
                payload["internet_status"] = "UNAVAILABLE"
                payload["mqtt_connection_status"] = "DISCONNECTED"
                outbox.enqueue(payload)

            mqtt_manager = FakeMQTTManager()
            ack_manager = AckManager("usv-01", outbox)
            events = CapturingEvents()
            synchronizer = OutboxSynchronizer(
                SimpleNamespace(
                    ack_timeout_seconds=3.0,
                    sync_batch_size=10,
                ),
                outbox,
                mqtt_manager,
                ack_manager,
                delivery_enabled_provider=lambda: True,
                terminal_events=events,
            )

            first = synchronizer.process_once()
            self.assertEqual(first.published_count, 10)
            self.assertTrue(synchronizer.recovery_active)
            self.assertEqual(
                [p["seq_id"] for p in mqtt_manager.published_payloads],
                list(range(1, 11)),
            )

            for seq_id in range(1, 11):
                self.assertTrue(
                    ack_manager.process_message(make_ack(seq_id)).removed
                )
            second = synchronizer.process_once()
            self.assertEqual(second.published_count, 10)
            self.assertTrue(synchronizer.recovery_active)

            for seq_id in range(11, 21):
                self.assertTrue(
                    ack_manager.process_message(make_ack(seq_id)).removed
                )
            third = synchronizer.process_once()
            self.assertEqual(third.published_count, 3)
            self.assertTrue(synchronizer.recovery_active)

            for seq_id in range(21, 24):
                self.assertTrue(
                    ack_manager.process_message(make_ack(seq_id)).removed
                )
            synchronizer.process_once()

            self.assertEqual(outbox.count(), 0)
            self.assertFalse(synchronizer.recovery_active)
            self.assertEqual(
                [p["seq_id"] for p in mqtt_manager.published_payloads],
                list(range(1, 24)),
            )

            batch_events = [
                message
                for _, category, message in events.events
                if category == "BATCH"
            ]
            self.assertEqual(len(batch_events), 3)
            self.assertIn("Batch 1/3: 10 records ACK", batch_events[0])
            self.assertIn("Batch 2/3: 10 records ACK", batch_events[1])
            self.assertIn("Batch 3/3: 3 records ACK", batch_events[2])

    def test_internet_loss_immediately_pauses_in_flight(self) -> None:
        with TemporaryDirectory() as directory:
            outbox = PersistentOutbox(Path(directory) / "outbox.sqlite3")
            for seq_id in range(1, 4):
                outbox.enqueue(make_payload(seq_id))

            internet_available = True
            synchronizer = OutboxSynchronizer(
                SimpleNamespace(
                    ack_timeout_seconds=3.0,
                    sync_batch_size=10,
                ),
                outbox,
                FakeMQTTManager(),
                AckManager("usv-01", outbox),
                delivery_enabled_provider=lambda: internet_available,
            )
            synchronizer.process_once()
            self.assertEqual(
                outbox.count_by_status(OUTBOX_IN_FLIGHT),
                3,
            )

            internet_available = False
            synchronizer.process_once()
            self.assertEqual(synchronizer.in_flight_count, 0)
            self.assertEqual(
                outbox.count_by_status(OUTBOX_IN_FLIGHT),
                0,
            )
            self.assertEqual(
                outbox.count_by_status(OUTBOX_PENDING),
                3,
            )
            self.assertFalse(synchronizer.recovery_active)

    def test_normal_pending_never_activates_recovery(self) -> None:
        with TemporaryDirectory() as directory:
            outbox = PersistentOutbox(Path(directory) / "outbox.sqlite3")
            for seq_id in range(1, 12):
                outbox.enqueue(make_payload(seq_id))

            mqtt_manager = FakeMQTTManager()
            synchronizer = OutboxSynchronizer(
                SimpleNamespace(
                    ack_timeout_seconds=3.0,
                    sync_batch_size=10,
                ),
                outbox,
                mqtt_manager,
                AckManager("usv-01", outbox),
            )
            synchronizer.process_once()
            self.assertFalse(synchronizer.recovery_active)
            self.assertTrue(
                all(
                    payload["delivery_type"] == "LIVE"
                    for payload in mqtt_manager.published_payloads
                )
            )
