"""Tests for windowed burst synchronization."""

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
from onboard.synchronizer import (
    SYNC_BURST_PUBLISHED,
    OutboxSynchronizer,
)
from tests.test_ack_manager import make_ack, make_payload


class FakeMQTTManager:
    is_connected = True

    def __init__(self) -> None:
        self.published_payloads: list[dict] = []

    def publish_telemetry(self, payload: dict) -> PublishResult:
        self.published_payloads.append(dict(payload))
        return PublishResult(
            accepted=True,
            topic=f"usv/{payload['vehicle_id']}/telemetry",
            rc=mqtt.MQTT_ERR_SUCCESS,
            message_id=len(self.published_payloads),
        )


def make_settings(window: int = 3):
    return SimpleNamespace(
        ack_timeout_seconds=3.0,
        sync_batch_size=window,
    )


class BurstSynchronizerTest(TestCase):

    def test_first_step_fills_window_oldest_first(self) -> None:
        with TemporaryDirectory() as directory:
            outbox = PersistentOutbox(Path(directory) / "outbox.sqlite3")

            for seq_id in range(1, 6):
                payload = make_payload(seq_id)
                payload["buffer_count"] = 5
                outbox.enqueue(payload)

            mqtt_manager = FakeMQTTManager()
            synchronizer = OutboxSynchronizer(
                make_settings(3),
                outbox,
                mqtt_manager,
                AckManager("usv-01", outbox),
            )

            result = synchronizer.process_once()

            self.assertEqual(result.action, SYNC_BURST_PUBLISHED)
            self.assertEqual(result.published_count, 3)
            self.assertEqual(synchronizer.in_flight_count, 3)
            self.assertEqual(
                [payload["seq_id"] for payload in mqtt_manager.published_payloads],
                [1, 2, 3],
            )
            self.assertEqual(
                outbox.count_by_status(OUTBOX_IN_FLIGHT),
                3,
            )
            self.assertEqual(
                outbox.count_by_status(OUTBOX_PENDING),
                2,
            )
            self.assertFalse(synchronizer.recovery_active)

    def test_ack_frees_slots_and_next_step_refills_them(self) -> None:
        with TemporaryDirectory() as directory:
            outbox = PersistentOutbox(Path(directory) / "outbox.sqlite3")

            for seq_id in range(1, 6):
                payload = make_payload(seq_id)
                payload["buffer_count"] = 5
                outbox.enqueue(payload)

            mqtt_manager = FakeMQTTManager()
            ack_manager = AckManager("usv-01", outbox)
            synchronizer = OutboxSynchronizer(
                make_settings(3),
                outbox,
                mqtt_manager,
                ack_manager,
            )

            synchronizer.process_once()

            self.assertTrue(ack_manager.process_message(make_ack(1)).removed)
            self.assertTrue(ack_manager.process_message(make_ack(2)).removed)

            result = synchronizer.process_once()

            self.assertEqual(result.published_count, 2)
            self.assertEqual(synchronizer.in_flight_count, 3)
            self.assertEqual(
                [payload["seq_id"] for payload in mqtt_manager.published_payloads],
                [1, 2, 3, 4, 5],
            )

    def test_in_flight_records_are_not_published_twice(self) -> None:
        with TemporaryDirectory() as directory:
            outbox = PersistentOutbox(Path(directory) / "outbox.sqlite3")

            for seq_id in range(1, 4):
                payload = make_payload(seq_id)
                payload["buffer_count"] = 3
                outbox.enqueue(payload)

            mqtt_manager = FakeMQTTManager()
            synchronizer = OutboxSynchronizer(
                make_settings(3),
                outbox,
                mqtt_manager,
                AckManager("usv-01", outbox),
            )

            synchronizer.process_once()
            synchronizer.process_once()

            self.assertEqual(
                [payload["seq_id"] for payload in mqtt_manager.published_payloads],
                [1, 2, 3],
            )
