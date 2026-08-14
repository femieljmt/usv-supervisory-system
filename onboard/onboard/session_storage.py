"""Per-session storage layout and metadata for onboard test runs.

Each application startup receives one unique session directory. Operation
records, runtime logs, mission plan, and metadata are grouped there so a
complete test run can be copied or archived without mixing it with another
run. The persistent outbox intentionally remains outside the session folder.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_SAFE_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class SessionPaths:
    """Absolute paths belonging to one onboard application session."""

    session_id: str
    directory: Path
    operation_file: Path
    runtime_file: Path
    mission_file: Path
    info_file: Path


class SessionStorage:
    """Create and atomically maintain one session directory and manifest."""

    def __init__(
        self,
        sessions_root: Path,
        session_id: str,
    ) -> None:
        cleaned_session = str(session_id).strip()

        if not cleaned_session:
            raise ValueError("session_id tidak boleh kosong")

        if not _SAFE_SESSION_PATTERN.fullmatch(cleaned_session):
            raise ValueError(
                "session_id hanya boleh berisi huruf, angka, '-' dan '_'"
            )

        root = Path(sessions_root).expanduser().resolve()
        directory = root / cleaned_session

        self.paths = SessionPaths(
            session_id=cleaned_session,
            directory=directory,
            operation_file=directory / "operation.csv",
            runtime_file=directory / "runtime.log",
            mission_file=directory / "mission_plan.json",
            info_file=directory / "session_info.json",
        )
        self._lock = threading.RLock()
        self._metadata: dict[str, Any] = {}

    def create(
        self,
        *,
        vehicle_id: str,
        outbox_path: Path,
        batch_size: int,
        mqtt_host: str,
        mqtt_port: int,
        mavlink_connection: str,
        initial_pending_outbox: int,
    ) -> SessionPaths:
        """Create the directory and initial session manifest."""

        with self._lock:
            self.paths.directory.mkdir(
                parents=True,
                exist_ok=False,
            )

            now_utc = datetime.now(timezone.utc)
            now_local = now_utc.astimezone()

            self._metadata = {
                "schema_version": 1,
                "vehicle_id": str(vehicle_id),
                "session_id": self.paths.session_id,
                "status": "RUNNING",
                "started_at_utc": _iso_timestamp(now_utc),
                "started_at_local": _iso_timestamp(now_local),
                "local_timezone": _timezone_name(now_local),
                "ended_at_utc": None,
                "ended_at_local": None,
                "exit_reason": None,
                "batch_size": int(batch_size),
                "mqtt_broker": f"{mqtt_host}:{int(mqtt_port)}",
                "mavlink_connection": str(mavlink_connection),
                "initial_pending_outbox": int(initial_pending_outbox),
                "final_pending_outbox": None,
                "files": {
                    "session_directory": str(self.paths.directory),
                    "operation_csv": str(self.paths.operation_file),
                    "runtime_log": str(self.paths.runtime_file),
                    "mission_plan": str(self.paths.mission_file),
                    "session_info": str(self.paths.info_file),
                    "persistent_outbox": str(Path(outbox_path).resolve()),
                },
            }
            self._write_manifest_locked()

        return self.paths

    def update(
        self,
        values: Mapping[str, Any],
    ) -> None:
        """Merge values into session_info.json using an atomic write."""

        with self._lock:
            if not self._metadata:
                self._metadata = self.read()

            self._metadata.update(dict(values))
            self._write_manifest_locked()

    def finalize(
        self,
        *,
        final_pending_outbox: int,
        exit_reason: str,
        status: str = "COMPLETED",
    ) -> None:
        """Record application shutdown information in the manifest."""

        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone()

        self.update(
            {
                "status": str(status),
                "ended_at_utc": _iso_timestamp(now_utc),
                "ended_at_local": _iso_timestamp(now_local),
                "exit_reason": str(exit_reason),
                "final_pending_outbox": int(final_pending_outbox),
            }
        )

    def read(self) -> dict[str, Any]:
        """Return the current session manifest."""

        with self.paths.info_file.open(
            "r",
            encoding="utf-8",
        ) as file_handle:
            value = json.load(file_handle)

        if not isinstance(value, dict):
            raise ValueError("session_info.json harus berupa object JSON")

        return value

    def _write_manifest_locked(self) -> None:
        temporary = self.paths.info_file.with_suffix(".json.tmp")
        encoded = json.dumps(
            self._metadata,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        with temporary.open(
            "w",
            encoding="utf-8",
        ) as file_handle:
            file_handle.write(encoded)
            file_handle.write("\n")
            file_handle.flush()
            os.fsync(file_handle.fileno())

        temporary.replace(self.paths.info_file)


def _iso_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _timezone_name(value: datetime) -> str:
    return value.tzname() or str(value.utcoffset() or "LOCAL")
