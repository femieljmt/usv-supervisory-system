"""Tests for persistent session and sequence management."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from onboard.sequence_manager import SequenceManager


class SequenceManagerTest(TestCase):

    def test_sequence_survives_restart(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory) / "outbox.sqlite3"
            )

            manager_one = SequenceManager(
                database_path,
                "usv-01",
            )
            state_one = manager_one.initialize()

            first = manager_one.next_identity()
            second = manager_one.next_identity()

            self.assertEqual(first.seq_id, 1)
            self.assertEqual(second.seq_id, 2)
            self.assertEqual(
                first.session_id,
                state_one.session_id,
            )

            manager_two = SequenceManager(
                database_path,
                "usv-01",
            )
            state_two = manager_two.initialize()
            third = manager_two.next_identity()

            self.assertEqual(
                state_two.session_id,
                state_one.session_id,
            )
            self.assertEqual(state_two.last_seq_id, 2)
            self.assertEqual(third.seq_id, 3)

    def test_vehicle_id_mismatch_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory) / "outbox.sqlite3"
            )

            SequenceManager(
                database_path,
                "usv-01",
            ).initialize()

            with self.assertRaises(RuntimeError):
                SequenceManager(
                    database_path,
                    "usv-02",
                ).initialize()
