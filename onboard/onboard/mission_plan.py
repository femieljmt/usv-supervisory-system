"""Mission-plan models, storage, and local JSON logging."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from onboard.protocol import PROTOCOL_VERSION


@dataclass(frozen=True)
class MissionItem:
    """One mission item downloaded from Pixhawk."""

    seq: int
    command: int
    command_name: str
    frame: int
    frame_name: str

    is_home: bool
    current: bool
    autocontinue: bool

    param1: float
    param2: float
    param3: float
    param4: float

    lat: float | None
    lon: float | None
    alt: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MissionPlanSnapshot:
    """Latest complete mission downloaded from Pixhawk."""

    revision: int = 0
    complete: bool = False
    downloaded_at: str | None = None
    opaque_id: int | None = None
    items: tuple[MissionItem, ...] = ()
    error: str | None = None

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def executable_count(self) -> int:
        return sum(
            1
            for item in self.items
            if not item.is_home
        )

    def to_payload(
        self,
        *,
        vehicle_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        if not self.complete:
            raise ValueError(
                "Mission plan belum selesai diunduh"
            )

        return {
            "protocol_version": PROTOCOL_VERSION,
            "event_type": "MISSION_PLAN",
            "vehicle_id": vehicle_id,
            "session_id": session_id,
            "generated_at": self.downloaded_at,
            "revision": self.revision,
            "mission_total": self.total_count,
            "executable_total": (
                self.executable_count
            ),
            "opaque_id": self.opaque_id,
            "waypoints": [
                item.to_dict()
                for item in self.items
            ],
        }


class MissionPlanStore:
    """Thread-safe storage for the latest mission plan."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot = MissionPlanSnapshot()

    def get_snapshot(self) -> MissionPlanSnapshot:
        with self._lock:
            return self._snapshot

    def set_complete(
        self,
        items: list[MissionItem],
        *,
        opaque_id: int | None = None,
    ) -> MissionPlanSnapshot:
        ordered_items = tuple(
            sorted(
                items,
                key=lambda item: item.seq,
            )
        )

        with self._lock:
            current = self._snapshot

            # Mission identity is based on the stable mission definition.
            # The MAVLink `current` flag may change while the vehicle advances
            # through a mission and must not be treated as a new upload.
            # `opaque_id` is useful metadata, but a changed/unstable value alone
            # must not create a new revision when waypoint content is identical.
            if (
                current.complete
                and _mission_signature(current.items)
                == _mission_signature(ordered_items)
            ):
                return current

            self._snapshot = MissionPlanSnapshot(
                revision=current.revision + 1,
                complete=True,
                downloaded_at=utcnow_iso(),
                opaque_id=opaque_id,
                items=ordered_items,
                error=None,
            )

            return self._snapshot

    def set_error(
        self,
        error: str,
    ) -> MissionPlanSnapshot:
        with self._lock:
            current = self._snapshot

            self._snapshot = MissionPlanSnapshot(
                revision=current.revision,
                complete=False,
                downloaded_at=current.downloaded_at,
                opaque_id=current.opaque_id,
                items=current.items,
                error=error,
            )

            return self._snapshot


def _mission_signature(items: tuple[MissionItem, ...]) -> tuple[tuple[Any, ...], ...]:
    """Return the stable mission definition used for change detection."""

    return tuple(
        (
            item.seq,
            item.command,
            item.frame,
            item.is_home,
            item.autocontinue,
            round(item.param1, 7),
            round(item.param2, 7),
            round(item.param3, 7),
            round(item.param4, 7),
            item.lat,
            item.lon,
            item.alt,
        )
        for item in items
    )


class MissionPlanLogger:
    """Store complete mission plans separately from operation CSV.

    ``file_path`` enables the per-session ``mission_plan.json`` layout.
    Without it, the legacy ``mission_plan_<session>.json`` naming remains
    available for compatibility. ``history_path`` may point to a global JSONL
    history file; pass ``None`` to disable aggregate history.
    """

    def __init__(
        self,
        log_directory: Path | None = None,
        *,
        file_path: Path | None = None,
        history_path: Path | None = None,
    ) -> None:
        if file_path is None and log_directory is None:
            raise ValueError(
                "log_directory atau file_path harus diberikan"
            )

        self._log_directory = (
            Path(log_directory)
            if log_directory is not None
            else Path(file_path).parent
        )
        self._file_path = (
            Path(file_path)
            if file_path is not None
            else None
        )
        self._history_path = (
            Path(history_path)
            if history_path is not None
            else (
                self._log_directory
                / "mission_plan_history.jsonl"
            )
        )

        self._log_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        if self._file_path is not None:
            self._file_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        if self._history_path is not None:
            self._history_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self._lock = threading.RLock()

    def write(
        self,
        payload: dict[str, Any],
    ) -> Path:
        session_id = str(
            payload["session_id"]
        )

        destination = self._destination_for_session(
            session_id
        )
        temporary = destination.with_suffix(
            destination.suffix + ".tmp"
        )

        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        with self._lock:
            with temporary.open(
                "w",
                encoding="utf-8",
            ) as file_handle:
                file_handle.write(encoded)
                file_handle.write("\n")
                file_handle.flush()
                os.fsync(file_handle.fileno())

            temporary.replace(destination)

            if self._history_path is not None:
                with self._history_path.open(
                    "a",
                    encoding="utf-8",
                ) as file_handle:
                    file_handle.write(
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                    file_handle.write("\n")
                    file_handle.flush()
                    os.fsync(file_handle.fileno())

        return destination

    def _destination_for_session(
        self,
        session_id: str,
    ) -> Path:
        if self._file_path is not None:
            return self._file_path

        safe_session = "".join(
            character
            if character.isalnum()
            or character in {"-", "_"}
            else "_"
            for character in session_id
        )

        return (
            self._log_directory
            / f"mission_plan_{safe_session}.json"
        )


def utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
