"""SQLite storage and read models for USV telemetry on Raspberry Pi Lab."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from server.protocol import validate_record_identity, validate_telemetry_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MIGRATION_PATH = PROJECT_ROOT / "migrations" / "001_initial_schema.sql"


@dataclass(frozen=True)
class StoreResult:
    record_id: int
    vehicle_id: str
    session_id: str
    seq_id: int
    inserted: bool
    duplicate: bool
    stored_at: str
    receive_count: int


class TelemetryDatabase:
    """Store validated telemetry and expose dashboard-safe read methods."""

    def __init__(
        self,
        database_path: Path,
        migration_path: Path | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.migration_path = Path(migration_path or DEFAULT_MIGRATION_PATH)
        self._initialize_lock = threading.RLock()
        self._initialized = False

    def initialize(self) -> None:
        """Create the database schema once per process."""

        if self._initialized:
            return

        with self._initialize_lock:
            if self._initialized:
                return

            if not self.migration_path.exists():
                raise FileNotFoundError(
                    f"File migration tidak ditemukan: {self.migration_path}"
                )

            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            migration_sql = self.migration_path.read_text(encoding="utf-8")

            with self._connection() as connection:
                connection.executescript(migration_sql)
                connection.commit()

            self._initialized = True

    def store_telemetry(
        self,
        payload: Mapping[str, Any],
        *,
        source_topic: str | None = None,
    ) -> StoreResult:
        """Store telemetry before an application ACK is published."""

        validate_telemetry_payload(payload)
        self._validate_database_fields(payload)
        self.initialize()

        vehicle_id = str(payload["vehicle_id"])
        session_id = str(payload["session_id"])
        seq_id = int(payload["seq_id"])
        validate_record_identity(vehicle_id, session_id, seq_id)

        payload_json = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        received_at = utcnow_iso()

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT id, backend_first_received_at, receive_count
                FROM telemetry_records
                WHERE vehicle_id = ? AND session_id = ? AND seq_id = ?
                """,
                (vehicle_id, session_id, seq_id),
            ).fetchone()

            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO telemetry_records (
                        protocol_version, vehicle_id, session_id, seq_id,
                        record_timestamp, data_source, delivery_type,
                        supervisor_state, mission_status, ap_link,
                        internet_status, mqtt_connection_status,
                        mqtt_publish_status, ack_status, ack_latency_ms,
                        retry_count, buffer_count, sync_status, armed,
                        flight_mode, lat, lon, heading, groundspeed,
                        battery_v, battery_remaining_pct, gps_fix,
                        gps_fix_label, gps_hdop, wp_index, wp_total,
                        wp_dist, mission_loaded, mission_complete,
                        payload_json, backend_first_received_at,
                        backend_last_received_at, backend_store_status,
                        backend_ack_sent_at, receive_count,
                        duplicate_received, ack_send_count
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, 'STORED',
                        NULL, 1, 0, 0
                    )
                    """,
                    (
                        payload["protocol_version"],
                        vehicle_id,
                        session_id,
                        seq_id,
                        payload["timestamp"],
                        payload["data_source"],
                        payload["delivery_type"],
                        payload["supervisor_state"],
                        payload["mission_status"],
                        payload["ap_link"],
                        payload["internet_status"],
                        payload["mqtt_connection_status"],
                        payload["mqtt_publish_status"],
                        payload["ack_status"],
                        payload["ack_latency_ms"],
                        payload["retry_count"],
                        payload["buffer_count"],
                        payload["sync_status"],
                        bool_to_integer(payload["armed"], "armed"),
                        payload["flight_mode"],
                        payload["lat"],
                        payload["lon"],
                        payload["heading"],
                        payload["groundspeed"],
                        payload["battery_v"],
                        payload["battery_remaining_pct"],
                        payload["gps_fix"],
                        payload["gps_fix_label"],
                        payload["gps_hdop"],
                        payload["wp_index"],
                        payload["wp_total"],
                        payload["wp_dist"],
                        bool_to_integer(payload["mission_loaded"], "mission_loaded"),
                        bool_to_integer(payload["mission_complete"], "mission_complete"),
                        payload_json,
                        received_at,
                        received_at,
                    ),
                )
                record_id = int(cursor.lastrowid)
                inserted = True
                duplicate = False
                receive_count = 1
                stored_at = received_at
                receive_result = "INSERTED"
            else:
                record_id = int(existing["id"])
                receive_count = int(existing["receive_count"]) + 1
                connection.execute(
                    """
                    UPDATE telemetry_records
                    SET backend_last_received_at = ?,
                        backend_store_status = 'DUPLICATE',
                        receive_count = ?,
                        duplicate_received = 1
                    WHERE id = ?
                    """,
                    (received_at, receive_count, record_id),
                )
                inserted = False
                duplicate = True
                stored_at = str(existing["backend_first_received_at"])
                receive_result = "DUPLICATE"

            connection.execute(
                """
                INSERT INTO delivery_receipts (
                    telemetry_record_id, received_at, source_topic,
                    delivery_type, receive_result, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    received_at,
                    source_topic,
                    payload["delivery_type"],
                    receive_result,
                    payload_json,
                ),
            )
            connection.commit()

        return StoreResult(
            record_id=record_id,
            vehicle_id=vehicle_id,
            session_id=session_id,
            seq_id=seq_id,
            inserted=inserted,
            duplicate=duplicate,
            stored_at=stored_at,
            receive_count=receive_count,
        )

    def mark_ack_sent(
        self,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
        *,
        sent_at: str | None = None,
    ) -> bool:
        validate_record_identity(vehicle_id, session_id, seq_id)
        self.initialize()
        ack_sent_at = sent_at or utcnow_iso()

        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE telemetry_records
                SET backend_ack_sent_at = ?,
                    ack_send_count = ack_send_count + 1
                WHERE vehicle_id = ? AND session_id = ? AND seq_id = ?
                """,
                (ack_sent_at, vehicle_id, session_id, seq_id),
            )
            connection.commit()
            updated = cursor.rowcount == 1

        return updated

    def get_record(
        self,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
    ) -> dict[str, Any] | None:
        validate_record_identity(vehicle_id, session_id, seq_id)
        self.initialize()

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM telemetry_records
                WHERE vehicle_id = ? AND session_id = ? AND seq_id = ?
                """,
                (vehicle_id, session_id, seq_id),
            ).fetchone()
        return None if row is None else _row_to_dict(row)

    def count_records(self) -> int:
        return self._scalar_count("telemetry_records")

    def count_delivery_receipts(self) -> int:
        return self._scalar_count("delivery_receipts")

    def list_vehicles(self) -> list[str]:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT vehicle_id, MAX(backend_last_received_at) AS latest
                FROM telemetry_records
                GROUP BY vehicle_id
                ORDER BY latest DESC
                """
            ).fetchall()
        return [str(row["vehicle_id"]) for row in rows]

    def list_sessions(
        self,
        vehicle_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return sessions newest-first by telemetry/session time.

        Server receipt time is deliberately *not* the primary sort key. A Lab Pi
        with an incorrect system clock can receive a 20 July session while writing
        a 14 July ``backend_last_received_at`` value. Sorting by that field makes
        old sessions appear above the newest session. ``record_timestamp`` and the
        timestamp embedded in the canonical session ID remain stable across replay.
        """

        self.initialize()
        safe_limit = min(max(int(limit), 1), 1000)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    grouped.session_id,
                    grouped.record_count,
                    grouped.first_record_at,
                    grouped.last_record_at,
                    grouped.last_received_at,
                    grouped.min_seq_id,
                    grouped.max_seq_id,
                    grouped.data_sources,
                    (
                        SELECT latest.supervisor_state
                        FROM telemetry_records AS latest
                        WHERE latest.vehicle_id = ?
                          AND latest.session_id = grouped.session_id
                        ORDER BY latest.record_timestamp DESC,
                                 latest.seq_id DESC,
                                 latest.id DESC
                        LIMIT 1
                    ) AS final_state
                FROM (
                    SELECT
                        session_id,
                        COUNT(*) AS record_count,
                        MIN(record_timestamp) AS first_record_at,
                        MAX(record_timestamp) AS last_record_at,
                        MAX(backend_last_received_at) AS last_received_at,
                        MIN(seq_id) AS min_seq_id,
                        MAX(seq_id) AS max_seq_id,
                        GROUP_CONCAT(DISTINCT data_source) AS data_sources
                    FROM telemetry_records
                    WHERE vehicle_id = ?
                    GROUP BY session_id
                ) AS grouped
                ORDER BY grouped.first_record_at DESC,
                         grouped.session_id DESC
                LIMIT ?
                """,
                (vehicle_id, vehicle_id, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_telemetry(
        self,
        vehicle_id: str | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        self.initialize()
        query = "SELECT * FROM telemetry_records"
        clauses: list[str] = []
        parameters: list[Any] = []
        if vehicle_id:
            clauses.append("vehicle_id = ?")
            parameters.append(vehicle_id)
        if session_id:
            clauses.append("session_id = ?")
            parameters.append(session_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY record_timestamp DESC, seq_id DESC, id DESC LIMIT 1"

        with self._connection() as connection:
            row = connection.execute(query, tuple(parameters)).fetchone()
        return None if row is None else _row_to_dict(row)

    def get_recent_telemetry(
        self,
        vehicle_id: str,
        *,
        limit: int = 100,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.initialize()
        safe_limit = min(max(int(limit), 1), 20000)
        query = "SELECT * FROM telemetry_records WHERE vehicle_id = ?"
        parameters: list[Any] = [vehicle_id]
        if session_id:
            query += " AND session_id = ?"
            parameters.append(session_id)
        query += " ORDER BY record_timestamp DESC, seq_id DESC LIMIT ?"
        parameters.append(safe_limit)

        with self._connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()

        result = [_row_to_dict(row) for row in rows]
        result.reverse()
        return result

    def get_session_dashboard_records(
        self,
        vehicle_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Return lightweight ordered fields used by dashboard charts."""

        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    record_timestamp,
                    backend_last_received_at,
                    seq_id,
                    supervisor_state,
                    delivery_type,
                    buffer_count,
                    retry_count,
                    ack_latency_ms,
                    groundspeed,
                    battery_v,
                    internet_status,
                    mqtt_connection_status,
                    ack_status,
                    sync_status,
                    lat,
                    lon,
                    heading,
                    gps_fix,
                    gps_fix_label,
                    gps_hdop,
                    wp_index,
                    wp_total,
                    flight_mode,
                    armed,
                    payload_json
                FROM telemetry_records
                WHERE vehicle_id = ? AND session_id = ?
                ORDER BY seq_id ASC, id ASC
                """,
                (vehicle_id, session_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session_track_records(
        self,
        vehicle_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Return all ordered fields required by map and playback modes."""

        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    record_timestamp, backend_last_received_at, seq_id,
                    supervisor_state, delivery_type, lat, lon, heading,
                    groundspeed, gps_fix, gps_fix_label, gps_hdop,
                    wp_index, wp_total, flight_mode, armed, battery_v
                FROM telemetry_records
                WHERE vehicle_id = ? AND session_id = ?
                ORDER BY seq_id ASC, id ASC
                """,
                (vehicle_id, session_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session_sequence_ids(
        self,
        vehicle_id: str,
        session_id: str,
    ) -> list[int]:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT seq_id
                FROM telemetry_records
                WHERE vehicle_id = ? AND session_id = ?
                ORDER BY seq_id ASC
                """,
                (vehicle_id, session_id),
            ).fetchall()
        return [int(row["seq_id"]) for row in rows]

    def get_session_records(
        self,
        vehicle_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Return all unique telemetry records for one session in sequence order."""

        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM telemetry_records
                WHERE vehicle_id = ? AND session_id = ?
                ORDER BY seq_id ASC, id ASC
                """,
                (vehicle_id, session_id),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_vehicle_summary(self, vehicle_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_records,
                    COALESCE(SUM(duplicate_received), 0) AS duplicate_records,
                    COALESCE(SUM(ack_send_count), 0) AS ack_send_count,
                    COUNT(DISTINCT session_id) AS session_count,
                    MAX(backend_last_received_at) AS last_received_at
                FROM telemetry_records
                WHERE vehicle_id = ?
                """,
                (vehicle_id,),
            ).fetchone()
        return dict(row) if row is not None else {}

    def get_session_summary(
        self,
        vehicle_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Return server and supervisory evidence for one selected session."""

        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_records,
                    MIN(seq_id) AS min_seq_id,
                    MAX(seq_id) AS max_seq_id,
                    MIN(record_timestamp) AS first_record_at,
                    MAX(record_timestamp) AS last_record_at,
                    MAX(backend_last_received_at) AS last_received_at,
                    MAX(buffer_count) AS max_buffer_count,
                    COALESCE(SUM(retry_count), 0) AS retry_total,
                    MAX(retry_count) AS max_retry_count,
                    COALESCE(SUM(ack_send_count), 0) AS ack_send_count,
                    COALESCE(SUM(receive_count), 0) AS receive_count,
                    COALESCE(SUM(CASE WHEN delivery_type = 'LIVE' THEN 1 ELSE 0 END), 0) AS live_records,
                    COALESCE(SUM(CASE WHEN delivery_type = 'REPLAY' THEN 1 ELSE 0 END), 0) AS replay_records,
                    COALESCE(SUM(CASE WHEN supervisor_state = 'NORMAL' THEN 1 ELSE 0 END), 0) AS normal_records,
                    COALESCE(SUM(CASE WHEN supervisor_state = 'GCS_LOST' THEN 1 ELSE 0 END), 0) AS gcs_lost_records,
                    COALESCE(SUM(CASE WHEN supervisor_state = 'RECOVERY' THEN 1 ELSE 0 END), 0) AS recovery_records,
                    COALESCE(SUM(CASE WHEN supervisor_state = 'PIXHAWK_LOST' THEN 1 ELSE 0 END), 0) AS pixhawk_lost_records,
                    AVG(CASE WHEN ack_latency_ms IS NOT NULL THEN ack_latency_ms END) AS average_ack_latency_ms,
                    MAX(ack_latency_ms) AS max_ack_latency_ms,
                    GROUP_CONCAT(DISTINCT data_source) AS data_sources_csv
                FROM telemetry_records
                WHERE vehicle_id = ? AND session_id = ?
                """,
                (vehicle_id, session_id),
            ).fetchone()
            receipt = connection.execute(
                """
                SELECT
                    COUNT(*) AS delivery_receipts,
                    COALESCE(SUM(CASE WHEN delivery_receipts.receive_result = 'DUPLICATE' THEN 1 ELSE 0 END), 0) AS duplicate_deliveries,
                    COALESCE(SUM(CASE WHEN delivery_receipts.receive_result = 'INSERTED' THEN 1 ELSE 0 END), 0) AS inserted_deliveries
                FROM delivery_receipts
                JOIN telemetry_records
                  ON telemetry_records.id = delivery_receipts.telemetry_record_id
                WHERE telemetry_records.vehicle_id = ?
                  AND telemetry_records.session_id = ?
                """,
                (vehicle_id, session_id),
            ).fetchone()
        result = dict(row) if row is not None else {}
        if receipt is not None:
            result.update(dict(receipt))
        return result

    def get_state_history(
        self,
        vehicle_id: str,
        *,
        limit: int = 50,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return state transitions derived within one session when selected."""

        records = self.get_recent_telemetry(
            vehicle_id,
            limit=min(max(limit * 40, 200), 20000),
            session_id=session_id,
        )
        transitions: list[dict[str, Any]] = []
        previous_state: str | None = None
        previous_session: str | None = None

        for record in records:
            current_session = str(record["session_id"])
            current_state = str(record["supervisor_state"])
            if current_session != previous_session:
                previous_state = None
                previous_session = current_session
            if current_state != previous_state:
                transitions.append(
                    {
                        "timestamp": record["record_timestamp"],
                        "received_at": record["backend_last_received_at"],
                        "seq_id": record["seq_id"],
                        "from_state": previous_state,
                        "to_state": current_state,
                        "session_id": current_session,
                    }
                )
                previous_state = current_state

        return transitions[-max(1, int(limit)):]

    def _scalar_count(self, table_name: str) -> int:
        if table_name not in {"telemetry_records", "delivery_receipts"}:
            raise ValueError("Nama tabel count tidak valid")
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS total FROM {table_name}"
            ).fetchone()
        return int(row["total"])

    def _validate_database_fields(self, payload: Mapping[str, Any]) -> None:
        for field_name in ("timestamp", "data_source", "mission_status", "flight_mode"):
            value = payload[field_name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} harus berupa string yang tidak kosong")

        for field_name in ("retry_count", "buffer_count", "wp_index", "wp_total"):
            value = payload[field_name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} harus berupa integer")
            if value < 0:
                raise ValueError(f"{field_name} tidak boleh negatif")

        bool_to_integer(payload["armed"], "armed")
        bool_to_integer(payload["mission_loaded"], "mission_loaded")
        bool_to_integer(payload["mission_complete"], "mission_complete")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
        finally:
            connection.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for name in ("armed", "mission_loaded", "mission_complete", "duplicate_received"):
        if name in result and result[name] is not None:
            result[name] = bool(result[name])
    return result


def bool_to_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} harus berupa boolean")
    return 1 if value else 0


def utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
