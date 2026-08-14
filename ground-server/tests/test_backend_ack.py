"""Tests for MQTT telemetry processing and application ACK."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from server.database import TelemetryDatabase
from server.mqtt_backend import (
    TelemetryMessageProcessor,
    extract_vehicle_id,
)
from tests.test_database import make_payload


class FakeAckPublisher:
    def __init__(
        self,
        *,
        publish_success: bool = True,
    ) -> None:
        self.publish_success = publish_success
        self.published_payloads: list[dict] = []

    def publish_ack(self, payload: dict) -> bool:
        self.published_payloads.append(dict(payload))
        return self.publish_success


class BackendAckTest(TestCase):

    def test_valid_record_is_stored_before_ack(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = TelemetryDatabase(
                Path(temporary_directory)
                / "server.sqlite3"
            )
            publisher = FakeAckPublisher()

            processor = TelemetryMessageProcessor(
                database,
                publisher,
            )

            payload = make_payload(1)

            result = processor.process(
                "usv/usv-01/telemetry",
                json.dumps(payload).encode("utf-8"),
            )

            self.assertTrue(result.store_result.inserted)
            self.assertTrue(result.ack_published)
            self.assertEqual(database.count_records(), 1)
            self.assertEqual(
                len(publisher.published_payloads),
                1,
            )

            ack = publisher.published_payloads[0]

            self.assertEqual(ack["vehicle_id"], "usv-01")
            self.assertEqual(
                ack["session_id"],
                "session-test",
            )
            self.assertEqual(ack["seq_id"], 1)
            self.assertEqual(ack["status"], "ACKED")

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

    def test_invalid_json_is_not_stored_or_acked(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = TelemetryDatabase(
                Path(temporary_directory)
                / "server.sqlite3"
            )
            publisher = FakeAckPublisher()

            processor = TelemetryMessageProcessor(
                database,
                publisher,
            )

            with self.assertRaises(ValueError):
                processor.process(
                    "usv/usv-01/telemetry",
                    b"{invalid-json",
                )

            database.initialize()

            self.assertEqual(database.count_records(), 0)
            self.assertEqual(
                len(publisher.published_payloads),
                0,
            )

    def test_topic_vehicle_mismatch_is_rejected(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = TelemetryDatabase(
                Path(temporary_directory)
                / "server.sqlite3"
            )
            publisher = FakeAckPublisher()

            processor = TelemetryMessageProcessor(
                database,
                publisher,
            )

            payload = make_payload(1)

            with self.assertRaises(ValueError):
                processor.process(
                    "usv/usv-02/telemetry",
                    json.dumps(payload).encode("utf-8"),
                )

            database.initialize()

            self.assertEqual(database.count_records(), 0)
            self.assertEqual(
                len(publisher.published_payloads),
                0,
            )

    def test_duplicate_record_receives_ack_again(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = TelemetryDatabase(
                Path(temporary_directory)
                / "server.sqlite3"
            )
            publisher = FakeAckPublisher()

            processor = TelemetryMessageProcessor(
                database,
                publisher,
            )

            raw_payload = json.dumps(
                make_payload(1)
            ).encode("utf-8")

            first = processor.process(
                "usv/usv-01/telemetry",
                raw_payload,
            )

            second = processor.process(
                "usv/usv-01/telemetry",
                raw_payload,
            )

            self.assertTrue(first.store_result.inserted)
            self.assertTrue(
                second.store_result.duplicate
            )

            self.assertEqual(database.count_records(), 1)
            self.assertEqual(
                database.count_delivery_receipts(),
                2,
            )
            self.assertEqual(
                len(publisher.published_payloads),
                2,
            )

    def test_failed_ack_publish_keeps_stored_record(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = TelemetryDatabase(
                Path(temporary_directory)
                / "server.sqlite3"
            )
            publisher = FakeAckPublisher(
                publish_success=False
            )

            processor = TelemetryMessageProcessor(
                database,
                publisher,
            )

            result = processor.process(
                "usv/usv-01/telemetry",
                json.dumps(
                    make_payload(1)
                ).encode("utf-8"),
            )

            self.assertTrue(result.store_result.inserted)
            self.assertFalse(result.ack_published)
            self.assertEqual(database.count_records(), 1)

            record = database.get_record(
                "usv-01",
                "session-test",
                1,
            )

            self.assertIsNone(
                record["backend_ack_sent_at"]
            )
            self.assertEqual(
                record["ack_send_count"],
                0,
            )

    def test_topic_parser(self) -> None:
        self.assertEqual(
            extract_vehicle_id(
                "usv/usv-01/telemetry"
            ),
            "usv-01",
        )

        with self.assertRaises(ValueError):
            extract_vehicle_id(
                "usv/usv-01/status"
            )
