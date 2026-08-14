"""Persistent session and sequence management for Raspberry Pi USV.

Identitas setiap record terdiri dari:

    vehicle_id + session_id + seq_id

Setiap startup aplikasi dapat membuat session baru melalui
start_new_session(). Record pertama pada session baru akan memperoleh
seq_id=1.

Session dan sequence disimpan dalam SQLite agar operasi increment tetap
atomik dan aman digunakan bersama persistent outbox.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


TABLE_NAME = "sequence_state"

REQUIRED_COLUMNS = {
    "singleton",
    "vehicle_id",
    "session_id",
    "last_seq_id",
    "updated_at",
}


@dataclass(frozen=True)
class RecordIdentity:
    """Unique identity allocated to one operation record."""

    vehicle_id: str
    session_id: str
    seq_id: int


@dataclass(frozen=True)
class SequenceState:
    """Current persistent sequence state."""

    vehicle_id: str
    session_id: str
    last_seq_id: int


class SequenceManager:
    """Manage persistent session IDs and atomic sequence allocation."""

    def __init__(
        self,
        database_path: str | Path,
        vehicle_id: str,
    ) -> None:
        self._database_path = Path(
            database_path
        ).expanduser().resolve()

        self._vehicle_id = vehicle_id.strip()

        if not self._vehicle_id:
            raise ValueError(
                "vehicle_id tidak boleh kosong"
            )

        if any(
            character in self._vehicle_id
            for character in {"/", "+", "#"}
        ):
            raise ValueError(
                "vehicle_id tidak boleh mengandung /, +, atau #"
            )

        self._database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._lock = threading.RLock()

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def vehicle_id(self) -> str:
        return self._vehicle_id

    def initialize(self) -> SequenceState:
        """Create the sequence table and initial state when required.

        Method ini tidak membuat session baru setiap kali dipanggil.
        Session baru untuk startup dibuat secara eksplisit menggunakan
        start_new_session().
        """

        with self._lock:
            connection = self._connect()

            try:
                self._prepare_schema(connection)

                connection.execute(
                    "BEGIN IMMEDIATE"
                )

                row = connection.execute(
                    f"""
                    SELECT
                        vehicle_id,
                        session_id,
                        last_seq_id
                    FROM {TABLE_NAME}
                    WHERE singleton = 1
                    """
                ).fetchone()

                if row is None:
                    session_id = generate_session_id()

                    connection.execute(
                        f"""
                        INSERT INTO {TABLE_NAME} (
                            singleton,
                            vehicle_id,
                            session_id,
                            last_seq_id,
                            updated_at
                        )
                        VALUES (1, ?, ?, 0, ?)
                        """,
                        (
                            self._vehicle_id,
                            session_id,
                            utcnow_iso(),
                        ),
                    )

                    state = SequenceState(
                        vehicle_id=self._vehicle_id,
                        session_id=session_id,
                        last_seq_id=0,
                    )

                else:
                    self._validate_stored_vehicle(
                        str(row["vehicle_id"])
                    )

                    state = SequenceState(
                        vehicle_id=str(
                            row["vehicle_id"]
                        ),
                        session_id=str(
                            row["session_id"]
                        ),
                        last_seq_id=int(
                            row["last_seq_id"]
                        ),
                    )

                connection.commit()
                return state

            except Exception:
                connection.rollback()
                raise

            finally:
                connection.close()

    def get_state(self) -> SequenceState:
        """Return the current persistent sequence state."""

        return self.initialize()

    def start_new_session(self) -> SequenceState:
        """Create a new session and reset its sequence counter.

        Record lama di persistent outbox tidak diubah. Record tersebut
        tetap memiliki session_id dan seq_id lama di dalam payloadnya.

        Record pertama yang dialokasikan setelah method ini dipanggil
        akan memperoleh seq_id=1.
        """

        self.initialize()

        with self._lock:
            connection = self._connect()

            try:
                self._prepare_schema(connection)

                connection.execute(
                    "BEGIN IMMEDIATE"
                )

                new_session_id = (
                    generate_session_id()
                )

                cursor = connection.execute(
                    f"""
                    UPDATE {TABLE_NAME}
                    SET
                        vehicle_id = ?,
                        session_id = ?,
                        last_seq_id = 0,
                        updated_at = ?
                    WHERE singleton = 1
                    """,
                    (
                        self._vehicle_id,
                        new_session_id,
                        utcnow_iso(),
                    ),
                )

                if cursor.rowcount != 1:
                    connection.execute(
                        f"""
                        INSERT INTO {TABLE_NAME} (
                            singleton,
                            vehicle_id,
                            session_id,
                            last_seq_id,
                            updated_at
                        )
                        VALUES (1, ?, ?, 0, ?)
                        """,
                        (
                            self._vehicle_id,
                            new_session_id,
                            utcnow_iso(),
                        ),
                    )

                connection.commit()

                return SequenceState(
                    vehicle_id=self._vehicle_id,
                    session_id=new_session_id,
                    last_seq_id=0,
                )

            except Exception:
                connection.rollback()
                raise

            finally:
                connection.close()

    def next_identity(self) -> RecordIdentity:
        """Atomically allocate the next identity in the active session."""

        self.initialize()

        with self._lock:
            connection = self._connect()

            try:
                self._prepare_schema(connection)

                connection.execute(
                    "BEGIN IMMEDIATE"
                )

                row = connection.execute(
                    f"""
                    SELECT
                        vehicle_id,
                        session_id,
                        last_seq_id
                    FROM {TABLE_NAME}
                    WHERE singleton = 1
                    """
                ).fetchone()

                if row is None:
                    raise RuntimeError(
                        "sequence_state belum diinisialisasi"
                    )

                stored_vehicle_id = str(
                    row["vehicle_id"]
                )

                self._validate_stored_vehicle(
                    stored_vehicle_id
                )

                session_id = str(
                    row["session_id"]
                )

                last_seq_id = int(
                    row["last_seq_id"]
                )

                if last_seq_id < 0:
                    raise RuntimeError(
                        "last_seq_id pada database tidak valid"
                    )

                next_seq_id = last_seq_id + 1

                cursor = connection.execute(
                    f"""
                    UPDATE {TABLE_NAME}
                    SET
                        last_seq_id = ?,
                        updated_at = ?
                    WHERE
                        singleton = 1
                        AND session_id = ?
                        AND last_seq_id = ?
                    """,
                    (
                        next_seq_id,
                        utcnow_iso(),
                        session_id,
                        last_seq_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "Gagal mengalokasikan seq_id secara atomik"
                    )

                connection.commit()

                return RecordIdentity(
                    vehicle_id=stored_vehicle_id,
                    session_id=session_id,
                    seq_id=next_seq_id,
                )

            except Exception:
                connection.rollback()
                raise

            finally:
                connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=10.0,
            isolation_level=None,
        )

        connection.row_factory = sqlite3.Row

        connection.execute(
            "PRAGMA foreign_keys = ON"
        )
        connection.execute(
            "PRAGMA busy_timeout = 10000"
        )

        return connection

    def _prepare_schema(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Create or repair the sequence table schema."""

        table_exists = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (TABLE_NAME,),
        ).fetchone()

        if table_exists is not None:
            columns = {
                str(row["name"])
                for row in connection.execute(
                    f"PRAGMA table_info({TABLE_NAME})"
                ).fetchall()
            }

            if not REQUIRED_COLUMNS.issubset(
                columns
            ):
                backup_table = (
                    f"{TABLE_NAME}_legacy_"
                    f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_"
                    f"{uuid.uuid4().hex[:6]}"
                )

                connection.execute(
                    f"""
                    ALTER TABLE {TABLE_NAME}
                    RENAME TO {backup_table}
                    """
                )

        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                singleton INTEGER PRIMARY KEY
                    CHECK (singleton = 1),

                vehicle_id TEXT NOT NULL,

                session_id TEXT NOT NULL,

                last_seq_id INTEGER NOT NULL
                    DEFAULT 0
                    CHECK (last_seq_id >= 0),

                updated_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_{TABLE_NAME}_session
            ON {TABLE_NAME} (
                vehicle_id,
                session_id
            )
            """
        )

    def _validate_stored_vehicle(
        self,
        stored_vehicle_id: str,
    ) -> None:
        if stored_vehicle_id != self._vehicle_id:
            raise RuntimeError(
                "VEHICLE_ID database tidak sesuai konfigurasi: "
                f"database={stored_vehicle_id!r}, "
                f"config={self._vehicle_id!r}"
            )


def generate_session_id() -> str:
    """Generate a compact and unique startup session identifier."""

    timestamp = (
        datetime.now(timezone.utc)
        .strftime("%Y%m%dT%H%M%SZ")
    )

    random_suffix = uuid.uuid4().hex[:6]

    return f"{timestamp}-{random_suffix}"


def utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
