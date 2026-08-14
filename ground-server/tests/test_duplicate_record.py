"""Tests for duplicate telemetry delivery."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from server.database import TelemetryDatabase
from tests.test_database import make_payload


class DuplicateTelemetryTest(TestCase):

    def test_duplicate_does_not_create_second_record(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory)
                / "server.sqlite3"
            )

            database = TelemetryDatabase(database_path)

            first = database.store_telemetry(
                make_payload(1),
                source_topic="usv/usv-01/telemetry",
            )

            replay_payload = make_payload(
                1,
                delivery_type="REPLAY",
            )

            second = database.store_telemetry(
                replay_payload,
                source_topic="usv/usv-01/telemetry",
            )

            self.assertTrue(first.inserted)
            self.assertFalse(first.duplicate)

            self.assertFalse(second.inserted)
            self.assertTrue(second.duplicate)

            self.assertEqual(
                database.count_records(),
                1,
            )

            self.assertEqual(
                database.count_delivery_receipts(),
                2,
            )

            record = database.get_record(
                "usv-01",
                "session-test",
                1,
            )

            self.assertEqual(
                record["receive_count"],
                2,
            )
            self.assertEqual(
                record["duplicate_received"],
                1,
            )
            self.assertEqual(
                record["backend_store_status"],
                "DUPLICATE",
            )

    def test_same_seq_id_in_different_session_is_valid(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory)
                / "server.sqlite3"
            )

            database = TelemetryDatabase(database_path)

            database.store_telemetry(
                make_payload(
                    1,
                    session_id="session-one",
                )
            )

            database.store_telemetry(
                make_payload(
                    1,
                    session_id="session-two",
                )
            )

            self.assertEqual(
                database.count_records(),
                2,
            )
