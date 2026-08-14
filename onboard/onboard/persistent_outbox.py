"""SQLite persistent outbox for USV telemetry records."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from onboard.protocol import (
    validate_record_identity,
    validate_telemetry_payload,
)


OUTBOX_PENDING = "PENDING"
OUTBOX_IN_FLIGHT = "IN_FLIGHT"

VALID_OUTBOX_STATUSES = {
    OUTBOX_PENDING,
    OUTBOX_IN_FLIGHT,
}


class DuplicateOutboxRecordError(RuntimeError):
    """Raised when the same record identity is inserted twice."""


@dataclass(frozen=True)
class OutboxRecord:
    row_id: int
    vehicle_id: str
    session_id: str
    seq_id: int
    payload: dict[str, Any]
    delivery_status: str
    retry_count: int
    created_at: str
    last_attempt_at: str | None


class PersistentOutbox:
    """Store telemetry until a matching application ACK is received."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._initialize_lock = threading.RLock()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return

        with self._initialize_lock:
            if self._initialized:
                return

            self.database_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with self._transaction() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS outbox (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        vehicle_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        seq_id INTEGER NOT NULL
                            CHECK (seq_id >= 1),
                        payload_json TEXT NOT NULL,
                        delivery_status TEXT NOT NULL
                            CHECK (
                                delivery_status IN (
                                    'PENDING',
                                    'IN_FLIGHT'
                                )
                            ),
                        retry_count INTEGER NOT NULL DEFAULT 0
                            CHECK (retry_count >= 0),
                        created_at TEXT NOT NULL,
                        last_attempt_at TEXT,
                        UNIQUE (
                            vehicle_id,
                            session_id,
                            seq_id
                        )
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_outbox_oldest
                    ON outbox (id)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_outbox_status
                    ON outbox (delivery_status)
                    """
                )

            self._initialized = True

    def enqueue(self, payload: Mapping[str, Any]) -> OutboxRecord:
        """Persist one telemetry payload before MQTT publication."""

        self.initialize()
        validate_telemetry_payload(payload)

        vehicle_id = payload["vehicle_id"]
        session_id = payload["session_id"]
        seq_id = payload["seq_id"]
        validate_record_identity(vehicle_id, session_id, seq_id)

        payload_json = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        created_at = utcnow_iso()

        try:
            with self._transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO outbox (
                        vehicle_id,
                        session_id,
                        seq_id,
                        payload_json,
                        delivery_status,
                        retry_count,
                        created_at,
                        last_attempt_at
                    )
                    VALUES (?, ?, ?, ?, ?, 0, ?, NULL)
                    """,
                    (
                        vehicle_id,
                        session_id,
                        seq_id,
                        payload_json,
                        OUTBOX_PENDING,
                        created_at,
                    ),
                )
                row_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise DuplicateOutboxRecordError(
                "Record dengan identity yang sama sudah berada "
                "di persistent outbox: "
                f"{vehicle_id}/{session_id}/{seq_id}"
            ) from exc

        return OutboxRecord(
            row_id=row_id,
            vehicle_id=vehicle_id,
            session_id=session_id,
            seq_id=seq_id,
            payload=dict(payload),
            delivery_status=OUTBOX_PENDING,
            retry_count=0,
            created_at=created_at,
            last_attempt_at=None,
        )

    def count(self) -> int:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM outbox"
            ).fetchone()
        return int(row["total"])

    def contains(
        self,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
    ) -> bool:
        validate_record_identity(vehicle_id, session_id, seq_id)
        self.initialize()

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM outbox
                WHERE vehicle_id = ?
                  AND session_id = ?
                  AND seq_id = ?
                """,
                (vehicle_id, session_id, seq_id),
            ).fetchone()

        return row is not None

    def get_pending_oldest(self, limit: int = 1) -> list[OutboxRecord]:
        """Return the oldest PENDING records only.

        This method is required by the windowed synchronizer so records that
        are already IN_FLIGHT are never published a second time while waiting
        for their application ACK.
        """

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit harus berupa integer")
        if limit < 1:
            raise ValueError("limit harus lebih besar atau sama dengan 1")

        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    vehicle_id,
                    session_id,
                    seq_id,
                    payload_json,
                    delivery_status,
                    retry_count,
                    created_at,
                    last_attempt_at
                FROM outbox
                WHERE delivery_status = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (OUTBOX_PENDING, limit),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def count_by_status(self, delivery_status: str) -> int:
        """Return the number of records in one outbox status."""

        if delivery_status not in {OUTBOX_PENDING, OUTBOX_IN_FLIGHT}:
            raise ValueError("delivery_status tidak valid")

        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM outbox
                WHERE delivery_status = ?
                """,
                (delivery_status,),
            ).fetchone()

        return int(row["total"])

    def get_oldest(self, limit: int = 1) -> list[OutboxRecord]:
        """Return the oldest records first."""

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit harus berupa integer")
        if limit < 1:
            raise ValueError("limit harus lebih besar atau sama dengan 1")

        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    vehicle_id,
                    session_id,
                    seq_id,
                    payload_json,
                    delivery_status,
                    retry_count,
                    created_at,
                    last_attempt_at
                FROM outbox
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def mark_attempt(
        self,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
        *,
        increment_retry: bool,
    ) -> bool:
        """Mark a publish attempt without deleting the record."""

        validate_record_identity(vehicle_id, session_id, seq_id)
        self.initialize()
        retry_increment = 1 if increment_retry else 0

        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE outbox
                SET delivery_status = ?,
                    retry_count = retry_count + ?,
                    last_attempt_at = ?
                WHERE vehicle_id = ?
                  AND session_id = ?
                  AND seq_id = ?
                """,
                (
                    OUTBOX_IN_FLIGHT,
                    retry_increment,
                    utcnow_iso(),
                    vehicle_id,
                    session_id,
                    seq_id,
                ),
            )
            changed = cursor.rowcount == 1

        return changed

    def mark_pending(
        self,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
    ) -> bool:
        """Return a record to PENDING after failure or ACK timeout."""

        validate_record_identity(vehicle_id, session_id, seq_id)
        self.initialize()

        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE outbox
                SET delivery_status = ?
                WHERE vehicle_id = ?
                  AND session_id = ?
                  AND seq_id = ?
                """,
                (
                    OUTBOX_PENDING,
                    vehicle_id,
                    session_id,
                    seq_id,
                ),
            )
            changed = cursor.rowcount == 1

        return changed

    def acknowledge(
        self,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
    ) -> bool:
        """Delete exactly one record after a valid application ACK."""

        validate_record_identity(vehicle_id, session_id, seq_id)
        self.initialize()

        with self._transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM outbox
                WHERE vehicle_id = ?
                  AND session_id = ?
                  AND seq_id = ?
                """,
                (vehicle_id, session_id, seq_id),
            )
            changed = cursor.rowcount == 1

        return changed

    def reset_in_flight_to_pending(self) -> int:
        """Recover records left IN_FLIGHT after an unexpected restart."""

        self.initialize()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE outbox
                SET delivery_status = ?
                WHERE delivery_status = ?
                """,
                (OUTBOX_PENDING, OUTBOX_IN_FLIGHT),
            )
            changed = int(cursor.rowcount)

        return changed


    def list_identities_by_supervisor_state(
        self,
        supervisor_state: str,
    ) -> list[tuple[str, str, int]]:
        """Return outbox identities whose stored payload has one state."""

        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT vehicle_id, session_id, seq_id, payload_json
                FROM outbox
                ORDER BY id ASC
                """
            ).fetchall()

        identities: list[tuple[str, str, int]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("supervisor_state") == supervisor_state:
                identities.append(
                    (
                        str(row["vehicle_id"]),
                        str(row["session_id"]),
                        int(row["seq_id"]),
                    )
                )
        return identities

    def get_pending_by_identities(
        self,
        identities: set[tuple[str, str, int]],
        *,
        limit: int,
    ) -> list[OutboxRecord]:
        """Return oldest PENDING records restricted to identities."""

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit harus berupa integer")
        if limit < 1:
            raise ValueError("limit harus lebih besar atau sama dengan 1")
        if not identities:
            return []

        records = self.get_pending_oldest(limit=max(limit, self.count()))
        selected = [
            record
            for record in records
            if (
                record.vehicle_id,
                record.session_id,
                record.seq_id,
            ) in identities
        ]
        return selected[:limit]

    def reset_identities_to_pending(
        self,
        identities: set[tuple[str, str, int]],
    ) -> int:
        """Return selected IN_FLIGHT records to PENDING."""

        if not identities:
            return 0

        changed = 0
        for vehicle_id, session_id, seq_id in identities:
            if self.mark_pending(vehicle_id, session_id, seq_id):
                changed += 1
        return changed

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> OutboxRecord:
        payload = json.loads(row["payload_json"])
        return OutboxRecord(
            row_id=int(row["id"]),
            vehicle_id=row["vehicle_id"],
            session_id=row["session_id"],
            seq_id=int(row["seq_id"]),
            payload=payload,
            delivery_status=row["delivery_status"],
            retry_count=int(row["retry_count"]),
            created_at=row["created_at"],
            last_attempt_at=row["last_attempt_at"],
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


def utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
