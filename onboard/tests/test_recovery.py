"""Tests for persistent outbox synchronization and recovery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
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
    SYNC_ACK_CONFIRMED,
    SYNC_ACK_TIMEOUT,
    SYNC_DELIVERY_DISABLED,
    SYNC_MQTT_DISCONNECTED,
    SYNC_PUBLISHED_LIVE,
    SYNC_PUBLISHED_REPLAY,
    SYNC_WAITING_ACK,
    OutboxSynchronizer,
)
from tests.test_ack_manager import (
    make_ack,
    make_payload,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeMQTTManager:
    def __init__(
        self,
        *,
        connected: bool = True,
        accepted: bool = True,
    ) -> None:
        self.is_connected = connected
        self.accepted = accepted
        self.published_payloads: list[dict] = []

    def publish_telemetry(
        self,
        payload: dict,
    ) -> PublishResult:
        self.published_payloads.append(
            dict(payload)
        )

        return PublishResult(
            accepted=self.accepted,
            topic=(
                f"usv/{payload['vehicle_id']}/telemetry"
            ),
            rc=(
                mqtt.MQTT_ERR_SUCCESS
                if self.accepted
                else mqtt.MQTT_ERR_NO_CONN
            ),
            message_id=(
                len(self.published_payloads)
                if self.accepted
                else None
            ),
        )


def make_settings():
    return SimpleNamespace(
        ack_timeout_seconds=3.0,
    )


class OutboxSynchronizerTest(TestCase):

    def test_first_delivery_is_live(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            outbox.enqueue(make_payload(1))

            mqtt_manager = FakeMQTTManager()
            ack_manager = AckManager(
                "usv-01",
                outbox,
            )
            clock = FakeClock()

            synchronizer = OutboxSynchronizer(
                make_settings(),
                outbox,
                mqtt_manager,
                ack_manager,
                monotonic_clock=clock,
            )

            result = synchronizer.process_once()

            self.assertEqual(
                result.action,
                SYNC_PUBLISHED_LIVE,
            )

            self.assertEqual(
                mqtt_manager
                .published_payloads[0]
                ["delivery_type"],
                "LIVE",
            )

            self.assertEqual(
                outbox.get_oldest()[0]
                .delivery_status,
                OUTBOX_IN_FLIGHT,
            )

            self.assertEqual(
                synchronizer.active_identity,
                (
                    "usv-01",
                    "session-test",
                    1,
                ),
            )

    def test_valid_ack_removes_record(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            outbox.enqueue(make_payload(1))

            mqtt_manager = FakeMQTTManager()
            ack_manager = AckManager(
                "usv-01",
                outbox,
            )

            synchronizer = OutboxSynchronizer(
                make_settings(),
                outbox,
                mqtt_manager,
                ack_manager,
            )

            first = synchronizer.process_once()

            self.assertEqual(
                first.action,
                SYNC_PUBLISHED_LIVE,
            )

            ack_result = (
                ack_manager.process_message(
                    make_ack(1)
                )
            )

            self.assertTrue(ack_result.removed)
            self.assertEqual(outbox.count(), 0)

            second = synchronizer.process_once()

            self.assertEqual(
                second.action,
                SYNC_ACK_CONFIRMED,
            )
            self.assertIsNone(
                synchronizer.active_identity
            )

    def test_waiting_before_ack_timeout(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            outbox.enqueue(make_payload(1))

            clock = FakeClock()

            synchronizer = OutboxSynchronizer(
                make_settings(),
                outbox,
                FakeMQTTManager(),
                AckManager(
                    "usv-01",
                    outbox,
                ),
                monotonic_clock=clock,
            )

            synchronizer.process_once()

            clock.advance(2.0)

            result = synchronizer.process_once()

            self.assertEqual(
                result.action,
                SYNC_WAITING_ACK,
            )
            self.assertEqual(outbox.count(), 1)

    def test_timeout_returns_pending_and_retry_remains_live(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            outbox.enqueue(make_payload(1))

            mqtt_manager = FakeMQTTManager()
            ack_manager = AckManager(
                "usv-01",
                outbox,
            )
            clock = FakeClock()

            synchronizer = OutboxSynchronizer(
                make_settings(),
                outbox,
                mqtt_manager,
                ack_manager,
                monotonic_clock=clock,
            )

            first = synchronizer.process_once()

            self.assertEqual(
                first.action,
                SYNC_PUBLISHED_LIVE,
            )

            clock.advance(3.1)

            timeout_result = (
                synchronizer.process_once()
            )

            self.assertEqual(
                timeout_result.action,
                SYNC_ACK_TIMEOUT,
            )

            pending_record = (
                outbox.get_oldest()[0]
            )

            self.assertEqual(
                pending_record.delivery_status,
                OUTBOX_PENDING,
            )

            retry_result = (
                synchronizer.process_once()
            )

            self.assertEqual(
                retry_result.action,
                SYNC_PUBLISHED_LIVE,
            )

            replay_payload = (
                mqtt_manager
                .published_payloads[1]
            )

            self.assertEqual(
                replay_payload["delivery_type"],
                "LIVE",
            )
            self.assertEqual(
                replay_payload["retry_count"],
                1,
            )
            self.assertEqual(
                replay_payload["sync_status"],
                "IDLE",
            )

    def test_oldest_record_is_sent_first(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            outbox.enqueue(make_payload(1))
            outbox.enqueue(make_payload(2))

            mqtt_manager = FakeMQTTManager()

            synchronizer = OutboxSynchronizer(
                make_settings(),
                outbox,
                mqtt_manager,
                AckManager(
                    "usv-01",
                    outbox,
                ),
            )

            synchronizer.process_once()

            self.assertEqual(
                mqtt_manager
                .published_payloads[0]
                ["seq_id"],
                1,
            )

    def test_disconnected_mqtt_does_not_mark_in_flight(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            outbox.enqueue(make_payload(1))

            mqtt_manager = FakeMQTTManager(
                connected=False
            )

            synchronizer = OutboxSynchronizer(
                make_settings(),
                outbox,
                mqtt_manager,
                AckManager(
                    "usv-01",
                    outbox,
                ),
            )

            result = synchronizer.process_once()

            self.assertEqual(
                result.action,
                SYNC_MQTT_DISCONNECTED,
            )

            record = outbox.get_oldest()[0]

            self.assertEqual(
                record.delivery_status,
                OUTBOX_PENDING,
            )
            self.assertEqual(
                len(
                    mqtt_manager
                    .published_payloads
                ),
                0,
            )

    def test_delivery_can_be_disabled_by_state(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            outbox.enqueue(make_payload(1))

            mqtt_manager = FakeMQTTManager()

            synchronizer = OutboxSynchronizer(
                make_settings(),
                outbox,
                mqtt_manager,
                AckManager(
                    "usv-01",
                    outbox,
                ),
                delivery_enabled_provider=(
                    lambda: False
                ),
            )

            result = synchronizer.process_once()

            self.assertEqual(
                result.action,
                SYNC_DELIVERY_DISABLED,
            )
            self.assertEqual(
                len(
                    mqtt_manager
                    .published_payloads
                ),
                0,
            )
            self.assertEqual(outbox.count(), 1)

    def test_gcs_lost_record_activates_recovery(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            payload = make_payload(1)
            payload["supervisor_state"] = (
                "GCS_LOST"
            )
            payload["internet_status"] = (
                "UNAVAILABLE"
            )
            payload[
                "mqtt_connection_status"
            ] = "DISCONNECTED"

            outbox.enqueue(payload)

            mqtt_manager = FakeMQTTManager(
                connected=True
            )

            synchronizer = OutboxSynchronizer(
                make_settings(),
                outbox,
                mqtt_manager,
                AckManager(
                    "usv-01",
                    outbox,
                ),
                delivery_enabled_provider=(
                    lambda: True
                ),
            )

            # Buffer saja belum berarti RECOVERY.
            self.assertFalse(
                synchronizer.recovery_active
            )

            result = synchronizer.process_once()

            self.assertEqual(
                result.action,
                SYNC_PUBLISHED_REPLAY,
            )
            self.assertTrue(
                synchronizer.recovery_active
            )

            sent = (
                mqtt_manager
                .published_payloads[0]
            )

            self.assertEqual(
                sent["delivery_type"],
                "REPLAY",
            )
            self.assertEqual(
                sent["sync_status"],
                "SYNCING",
            )

    def test_new_record_can_be_added_while_waiting_ack(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            outbox.enqueue(make_payload(1))

            synchronizer = OutboxSynchronizer(
                make_settings(),
                outbox,
                FakeMQTTManager(),
                AckManager(
                    "usv-01",
                    outbox,
                ),
            )

            synchronizer.process_once()

            # Producer tetap dapat menyimpan data baru
            # walaupun record pertama menunggu ACK.
            outbox.enqueue(make_payload(2))

            self.assertEqual(outbox.count(), 2)

            oldest = outbox.get_oldest(
                limit=2
            )

            self.assertEqual(
                [
                    record.seq_id
                    for record in oldest
                ],
                [1, 2],
            )
