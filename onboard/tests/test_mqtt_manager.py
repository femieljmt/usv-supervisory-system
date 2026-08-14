"""Tests for onboard MQTT manager without a real broker."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest import TestCase

from paho.mqtt import client as mqtt

from onboard.ack_manager import AckManager
from onboard.mqtt_manager import MQTTManager
from onboard.persistent_outbox import PersistentOutbox
from tests.test_ack_manager import (
    make_ack,
    make_payload,
)


class FakeReasonCode:
    is_failure = False


class FakePublishInfo:
    def __init__(
        self,
        rc: int = mqtt.MQTT_ERR_SUCCESS,
        mid: int = 7,
    ) -> None:
        self.rc = rc
        self.mid = mid


class FakeMQTTClient:
    def __init__(self) -> None:
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

        self.subscribe_calls: list[tuple] = []
        self.publish_calls: list[tuple] = []

    def username_pw_set(
        self,
        username,
        password,
    ) -> None:
        pass

    def reconnect_delay_set(
        self,
        min_delay,
        max_delay,
    ) -> None:
        pass

    def connect_async(
        self,
        host,
        port,
        keepalive,
    ) -> None:
        pass

    def loop_start(self) -> None:
        pass

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def subscribe(
        self,
        topic,
        qos,
    ):
        self.subscribe_calls.append(
            (topic, qos)
        )
        return mqtt.MQTT_ERR_SUCCESS, 1

    def publish(
        self,
        topic,
        payload,
        qos,
        retain,
    ):
        self.publish_calls.append(
            (
                topic,
                payload,
                qos,
                retain,
            )
        )
        return FakePublishInfo()


def make_settings():
    return SimpleNamespace(
        vehicle_id="usv-01",
        mqtt_host="100.64.0.20",
        mqtt_port=1883,
        mqtt_username=None,
        mqtt_password=None,
        mqtt_keepalive=30,
        mqtt_qos=1,
    )


class MQTTManagerTest(TestCase):

    def test_publish_and_ack_callback(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            payload = make_payload(1)
            outbox.enqueue(payload)

            ack_manager = AckManager(
                "usv-01",
                outbox,
            )

            fake_client = FakeMQTTClient()

            manager = MQTTManager(
                make_settings(),
                ack_manager,
                mqtt_client=fake_client,
            )

            manager._on_connect(
                fake_client,
                None,
                None,
                FakeReasonCode(),
                None,
            )

            self.assertTrue(manager.is_connected)
            self.assertEqual(
                fake_client.subscribe_calls,
                [
                    (
                        "usv/usv-01/ack",
                        1,
                    )
                ],
            )

            result = manager.publish_telemetry(
                payload
            )

            self.assertTrue(result.accepted)
            self.assertEqual(
                result.topic,
                "usv/usv-01/telemetry",
            )
            self.assertEqual(
                ack_manager.pending_count(),
                1,
            )

            sent_topic = (
                fake_client.publish_calls[0][0]
            )
            sent_payload = json.loads(
                fake_client.publish_calls[0][1]
            )

            self.assertEqual(
                sent_topic,
                "usv/usv-01/telemetry",
            )
            self.assertEqual(
                sent_payload["seq_id"],
                1,
            )

            message = SimpleNamespace(
                topic="usv/usv-01/ack",
                payload=json.dumps(
                    make_ack(1)
                ).encode("utf-8"),
            )

            manager._on_message(
                fake_client,
                None,
                message,
            )

            self.assertEqual(outbox.count(), 0)
            self.assertEqual(
                ack_manager.pending_count(),
                0,
            )

    def test_publish_rejected_when_disconnected(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            outbox = PersistentOutbox(
                Path(temporary_directory)
                / "outbox.sqlite3"
            )

            ack_manager = AckManager(
                "usv-01",
                outbox,
            )

            manager = MQTTManager(
                make_settings(),
                ack_manager,
                mqtt_client=FakeMQTTClient(),
            )

            result = manager.publish_telemetry(
                make_payload(1)
            )

            self.assertFalse(result.accepted)
            self.assertEqual(
                result.rc,
                mqtt.MQTT_ERR_NO_CONN,
            )
            self.assertEqual(
                ack_manager.pending_count(),
                0,
            )
