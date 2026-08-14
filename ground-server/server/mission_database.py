"""SQLite storage for USV mission plans and waypoint lists."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class MissionStoreResult:
    plan_id: int
    inserted: bool
    duplicate: bool
    received_at: str
    payload_hash: str


class MissionDatabase:
    """Persistent SQLite storage for complete mission plans."""

    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialized = False

    @property
    def database_path(self) -> Path:
        return self._database_path

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            with self._connection() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS mission_plans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        payload_hash TEXT NOT NULL UNIQUE,
                        protocol_version TEXT NOT NULL,
                        vehicle_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        generated_at TEXT,
                        received_at TEXT NOT NULL,
                        mission_total INTEGER NOT NULL,
                        executable_total INTEGER NOT NULL,
                        opaque_id INTEGER,
                        payload_json TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_mission_plans_vehicle_received
                    ON mission_plans (vehicle_id, received_at DESC);

                    CREATE INDEX IF NOT EXISTS idx_mission_plans_session
                    ON mission_plans (vehicle_id, session_id);

                    CREATE TABLE IF NOT EXISTS mission_waypoints (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        plan_id INTEGER NOT NULL,
                        seq INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        command INTEGER NOT NULL,
                        command_name TEXT NOT NULL,
                        frame INTEGER NOT NULL,
                        frame_name TEXT NOT NULL,
                        is_home INTEGER NOT NULL,
                        is_current INTEGER NOT NULL,
                        autocontinue INTEGER NOT NULL,
                        param1 REAL NOT NULL,
                        param2 REAL NOT NULL,
                        param3 REAL NOT NULL,
                        param4 REAL NOT NULL,
                        latitude REAL,
                        longitude REAL,
                        altitude REAL,
                        FOREIGN KEY(plan_id) REFERENCES mission_plans(id) ON DELETE CASCADE,
                        UNIQUE(plan_id, seq)
                    );

                    CREATE INDEX IF NOT EXISTS idx_mission_waypoints_plan_seq
                    ON mission_waypoints (plan_id, seq);

                    CREATE TABLE IF NOT EXISTS latest_mission_plan (
                        vehicle_id TEXT PRIMARY KEY,
                        plan_id INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(plan_id) REFERENCES mission_plans(id) ON DELETE CASCADE
                    );
                    """
                )
                connection.commit()
            self._initialized = True

    def store(self, payload: dict[str, Any]) -> MissionStoreResult:
        normalized = validate_mission_payload(payload)
        received_at = utcnow_iso()
        encoded_payload = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = hashlib.sha256(encoded_payload.encode("utf-8")).hexdigest()

        self.initialize()
        with self._lock:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO mission_plans (
                        payload_hash, protocol_version, vehicle_id, session_id,
                        generated_at, received_at, mission_total,
                        executable_total, opaque_id, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload_hash,
                        normalized["protocol_version"],
                        normalized["vehicle_id"],
                        normalized["session_id"],
                        normalized.get("generated_at"),
                        received_at,
                        normalized["mission_total"],
                        normalized["executable_total"],
                        normalized.get("opaque_id"),
                        encoded_payload,
                    ),
                )
                inserted = cursor.rowcount == 1

                if inserted:
                    plan_id = int(cursor.lastrowid)
                    self._insert_waypoints(connection, plan_id, normalized["waypoints"])
                else:
                    existing = connection.execute(
                        "SELECT id FROM mission_plans WHERE payload_hash = ?",
                        (payload_hash,),
                    ).fetchone()
                    if existing is None:
                        raise RuntimeError("Mission plan duplicate tidak ditemukan")
                    plan_id = int(existing["id"])

                connection.execute(
                    """
                    INSERT INTO latest_mission_plan (vehicle_id, plan_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(vehicle_id) DO UPDATE SET
                        plan_id = excluded.plan_id,
                        updated_at = excluded.updated_at
                    """,
                    (normalized["vehicle_id"], plan_id, received_at),
                )
                connection.commit()

        return MissionStoreResult(
            plan_id=plan_id,
            inserted=inserted,
            duplicate=not inserted,
            received_at=received_at,
            payload_hash=payload_hash,
        )

    def get_latest(self, vehicle_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._lock:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json, received_at, payload_hash
                    FROM mission_plans
                    WHERE vehicle_id = ?
                    ORDER BY COALESCE(generated_at, '') DESC,
                             session_id DESC, id DESC
                    LIMIT 1
                    """,
                    (vehicle_id,),
                ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["received_at"] = row["received_at"]
        payload["payload_hash"] = row["payload_hash"]
        return payload

    def get_for_session(
        self,
        vehicle_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """Return the newest mission plan associated with a telemetry session."""

        self.initialize()
        with self._lock:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json, received_at, payload_hash
                    FROM mission_plans
                    WHERE vehicle_id = ? AND session_id = ?
                    ORDER BY COALESCE(generated_at, '') DESC, id DESC
                    LIMIT 1
                    """,
                    (vehicle_id, session_id),
                ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["received_at"] = row["received_at"]
        payload["payload_hash"] = row["payload_hash"]
        return payload

    def list_latest_vehicles(self) -> list[str]:
        self.initialize()
        with self._lock:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT vehicle_id FROM latest_mission_plan ORDER BY vehicle_id"
                ).fetchall()
        return [str(row["vehicle_id"]) for row in rows]

    def count_plans(self) -> int:
        return self._count("mission_plans")

    def count_waypoints(self) -> int:
        return self._count("mission_waypoints")

    def _count(self, table_name: str) -> int:
        if table_name not in {"mission_plans", "mission_waypoints"}:
            raise ValueError("Nama tabel mission tidak valid")
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS total FROM {table_name}"
            ).fetchone()
        return int(row["total"])

    def _insert_waypoints(
        self,
        connection: sqlite3.Connection,
        plan_id: int,
        waypoints: list[dict[str, Any]],
    ) -> None:
        for waypoint in waypoints:
            is_home = bool(waypoint.get("is_home"))
            connection.execute(
                """
                INSERT INTO mission_waypoints (
                    plan_id, seq, role, command, command_name, frame,
                    frame_name, is_home, is_current, autocontinue,
                    param1, param2, param3, param4,
                    latitude, longitude, altitude
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    waypoint["seq"],
                    "HOME" if is_home else "MISSION",
                    waypoint["command"],
                    waypoint["command_name"],
                    waypoint["frame"],
                    waypoint["frame_name"],
                    int(is_home),
                    int(bool(waypoint.get("current"))),
                    int(bool(waypoint.get("autocontinue"))),
                    waypoint["param1"],
                    waypoint["param2"],
                    waypoint["param3"],
                    waypoint["param4"],
                    waypoint.get("lat"),
                    waypoint.get("lon"),
                    waypoint.get("alt"),
                ),
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
        finally:
            connection.close()


def validate_mission_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Mission payload harus berupa object")
    if payload.get("event_type") != "MISSION_PLAN":
        raise ValueError("event_type harus MISSION_PLAN")

    protocol_version = required_text(payload, "protocol_version")
    vehicle_id = required_text(payload, "vehicle_id")
    session_id = required_text(payload, "session_id")
    waypoints = payload.get("waypoints")
    if not isinstance(waypoints, list):
        raise ValueError("waypoints harus berupa list")

    mission_total = required_nonnegative_int(payload, "mission_total")
    executable_total = required_nonnegative_int(payload, "executable_total")
    if mission_total != len(waypoints):
        raise ValueError("mission_total tidak sesuai jumlah waypoints")

    sequences: set[int] = set()
    executable_count = 0
    normalized_waypoints: list[dict[str, Any]] = []
    for index, waypoint in enumerate(waypoints):
        normalized = validate_waypoint(waypoint, index=index)
        sequence = normalized["seq"]
        if sequence in sequences:
            raise ValueError(f"seq waypoint duplicate: {sequence}")
        sequences.add(sequence)
        if not normalized["is_home"]:
            executable_count += 1
        normalized_waypoints.append(normalized)

    if executable_total != executable_count:
        raise ValueError("executable_total tidak sesuai waypoint non-HOME")

    normalized_waypoints.sort(key=lambda item: item["seq"])
    opaque_id = payload.get("opaque_id")
    if opaque_id is not None:
        opaque_id = int(opaque_id)
    revision = payload.get("revision", 0)
    try:
        revision = max(0, int(revision))
    except (TypeError, ValueError):
        revision = 0

    return {
        "protocol_version": protocol_version,
        "event_type": "MISSION_PLAN",
        "vehicle_id": vehicle_id,
        "session_id": session_id,
        "generated_at": payload.get("generated_at"),
        "revision": revision,
        "mission_total": mission_total,
        "executable_total": executable_total,
        "opaque_id": opaque_id,
        "waypoints": normalized_waypoints,
    }


def validate_waypoint(waypoint: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(waypoint, dict):
        raise ValueError(f"waypoint index {index} harus object")

    sequence = int(waypoint.get("seq", -1))
    if sequence < 0:
        raise ValueError(f"seq waypoint tidak valid pada index {index}")

    latitude = optional_float(waypoint.get("lat"))
    longitude = optional_float(waypoint.get("lon"))
    altitude = optional_float(waypoint.get("alt"))
    if latitude is not None and not -90.0 <= latitude <= 90.0:
        raise ValueError(f"latitude tidak valid pada seq {sequence}")
    if longitude is not None and not -180.0 <= longitude <= 180.0:
        raise ValueError(f"longitude tidak valid pada seq {sequence}")

    return {
        "seq": sequence,
        "command": int(waypoint.get("command", 0)),
        "command_name": str(waypoint.get("command_name", "UNKNOWN")),
        "frame": int(waypoint.get("frame", 0)),
        "frame_name": str(waypoint.get("frame_name", "UNKNOWN")),
        "is_home": bool(waypoint.get("is_home")),
        "current": bool(waypoint.get("current")),
        "autocontinue": bool(waypoint.get("autocontinue")),
        "param1": float(waypoint.get("param1", 0.0)),
        "param2": float(waypoint.get("param2", 0.0)),
        "param3": float(waypoint.get("param3", 0.0)),
        "param4": float(waypoint.get("param4", 0.0)),
        "lat": latitude,
        "lon": longitude,
        "alt": altitude,
    }


def required_text(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} harus berupa text yang tidak kosong")
    return value.strip()


def required_nonnegative_int(payload: dict[str, Any], field_name: str) -> int:
    try:
        value = int(payload[field_name])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{field_name} harus berupa integer") from error
    if value < 0:
        raise ValueError(f"{field_name} tidak boleh negatif")
    return value


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Nilai koordinat bukan angka: {value}") from error


def utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
