"""Tests for durable local CSV operation logging."""

from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from onboard.local_logger import (
    LocalOperationLogger,
    timestamp_date_tag,
)
from tests.test_ack_manager import make_payload


class LocalOperationLoggerTest(TestCase):

    def test_writes_header_and_record(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            logger = LocalOperationLogger(
                Path(temporary_directory),
                durable_write=False,
            )

            record = make_payload(1)

            path = logger.write(record)

            self.assertTrue(path.exists())
            self.assertEqual(
                path.name,
                "operation_20260710.csv",
            )

            with path.open(
                "r",
                newline="",
                encoding="utf-8",
            ) as csv_file:
                rows = list(
                    csv.DictReader(csv_file)
                )

            self.assertEqual(len(rows), 1)
            self.assertEqual(
                rows[0]["vehicle_id"],
                "usv-01",
            )
            self.assertEqual(
                rows[0]["seq_id"],
                "1",
            )

    def test_restart_appends_without_duplicate_header(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            log_directory = Path(
                temporary_directory
            )

            first_logger = LocalOperationLogger(
                log_directory,
                durable_write=False,
            )
            first_logger.write(make_payload(1))

            # Simulasi program dibuka ulang.
            second_logger = LocalOperationLogger(
                log_directory,
                durable_write=False,
            )
            second_logger.write(make_payload(2))

            path = (
                log_directory
                / "operation_20260710.csv"
            )

            lines = path.read_text(
                encoding="utf-8"
            ).splitlines()

            # Satu header dan dua record.
            self.assertEqual(len(lines), 3)

            header_occurrences = sum(
                1
                for line in lines
                if line.startswith(
                    "protocol_version,"
                )
            )

            self.assertEqual(
                header_occurrences,
                1,
            )

            with path.open(
                "r",
                newline="",
                encoding="utf-8",
            ) as csv_file:
                rows = list(
                    csv.DictReader(csv_file)
                )

            self.assertEqual(
                [
                    row["seq_id"]
                    for row in rows
                ],
                ["1", "2"],
            )

    def test_none_values_are_written_as_empty(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            logger = LocalOperationLogger(
                Path(temporary_directory),
                durable_write=False,
            )

            record = make_payload(1)
            record["lat"] = None
            record["battery_v"] = None

            path = logger.write(record)

            with path.open(
                "r",
                newline="",
                encoding="utf-8",
            ) as csv_file:
                row = next(
                    csv.DictReader(csv_file)
                )

            self.assertEqual(row["lat"], "")
            self.assertEqual(
                row["battery_v"],
                "",
            )


    def test_fixed_session_file_path(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            operation_file = (
                Path(temporary_directory)
                / "session-test"
                / "operation.csv"
            )
            logger = LocalOperationLogger(
                file_path=operation_file,
                durable_write=False,
            )

            first_path = logger.write(make_payload(1))
            second_path = logger.write(make_payload(2))

            self.assertEqual(first_path, operation_file)
            self.assertEqual(second_path, operation_file)
            self.assertTrue(operation_file.exists())

            with operation_file.open(
                "r",
                newline="",
                encoding="utf-8",
            ) as csv_file:
                rows = list(csv.DictReader(csv_file))

            self.assertEqual(
                [row["seq_id"] for row in rows],
                ["1", "2"],
            )

    def test_timestamp_date_tag(self) -> None:
        self.assertEqual(
            timestamp_date_tag(
                "2026-07-10T01:00:00.000Z"
            ),
            "20260710",
        )

        with self.assertRaises(ValueError):
            timestamp_date_tag(
                "timestamp-tidak-valid"
            )
