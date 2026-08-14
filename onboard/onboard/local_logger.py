"""Durable CSV local logging for Raspberry Pi USV operation records."""

from __future__ import annotations

import csv
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from onboard.protocol import (
    CSV_TELEMETRY_FIELDS,
    validate_telemetry_payload,
)


class LocalOperationLogger:
    """Append validated operation records to a durable CSV file.

    ``file_path`` is used by the final per-session layout and produces one
    ``operation.csv`` for one application startup. ``log_directory`` remains
    supported for compatibility with older daily-log tests and utilities.
    """

    def __init__(
        self,
        log_directory: Path | None = None,
        *,
        file_path: Path | None = None,
        durable_write: bool = True,
    ) -> None:
        if file_path is None and log_directory is None:
            raise ValueError(
                "log_directory atau file_path harus diberikan"
            )

        self.log_directory = (
            Path(log_directory)
            if log_directory is not None
            else Path(file_path).parent
        )
        self.file_path = (
            Path(file_path)
            if file_path is not None
            else None
        )
        self.durable_write = durable_write
        self._lock = threading.RLock()

        self.log_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        if self.file_path is not None:
            self.file_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

    def write(
        self,
        record: Mapping[str, Any],
    ) -> Path:
        """Append one record and return the CSV path used."""

        validate_telemetry_payload(record)

        log_path = self.path_for_timestamp(
            str(record["timestamp"])
        )

        with self._lock:
            log_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            needs_header = (
                not log_path.exists()
                or log_path.stat().st_size == 0
            )

            with log_path.open(
                "a",
                newline="",
                encoding="utf-8",
            ) as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=list(CSV_TELEMETRY_FIELDS),
                    extrasaction="ignore",
                )

                if needs_header:
                    writer.writeheader()

                writer.writerow(
                    self._normalized_row(record)
                )

                csv_file.flush()

                if self.durable_write:
                    os.fsync(csv_file.fileno())

        return log_path

    def path_for_timestamp(
        self,
        timestamp: str,
    ) -> Path:
        """Return the fixed session CSV or the legacy daily CSV path."""

        if self.file_path is not None:
            return self.file_path

        date_tag = timestamp_date_tag(timestamp)

        return (
            self.log_directory
            / f"operation_{date_tag}.csv"
        )

    @staticmethod
    def _normalized_row(
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Keep None values empty and preserve all protocol fields."""

        return {
            field: (
                ""
                if record.get(field) is None
                else record.get(field)
            )
            for field in CSV_TELEMETRY_FIELDS
        }


def timestamp_date_tag(timestamp: str) -> str:
    """Convert an ISO 8601 timestamp into YYYYMMDD."""

    if not isinstance(timestamp, str):
        raise ValueError(
            "timestamp harus berupa string"
        )

    cleaned = timestamp.strip()

    if not cleaned:
        raise ValueError(
            "timestamp tidak boleh kosong"
        )

    iso_value = (
        cleaned[:-1] + "+00:00"
        if cleaned.endswith("Z")
        else cleaned
    )

    try:
        parsed = datetime.fromisoformat(
            iso_value
        )
    except ValueError as exc:
        raise ValueError(
            f"timestamp ISO 8601 tidak valid: {timestamp!r}"
        ) from exc

    return parsed.strftime("%Y%m%d")
