"""Data models for Raspberry Pi USV telemetry."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class TelemetrySnapshot:
    """Latest telemetry values received from Pixhawk."""

    armed: bool = False
    flight_mode: str = "UNKNOWN"

    lat: float | None = None
    lon: float | None = None
    heading: float | None = None
    groundspeed: float | None = None

    battery_v: float | None = None
    battery_remaining_pct: int | None = None

    gps_fix: int | None = None
    gps_fix_label: str = "UNKNOWN"
    gps_hdop: float | None = None

    wp_index: int = 0
    wp_total: int = 0
    wp_dist: float | None = None

    mission_loaded: bool = False
    mission_complete: bool = False

    # Dynamic target reported by the autopilot while operating in GUIDED.
    guided_target_lat: float | None = None
    guided_target_lon: float | None = None
    guided_target_alt: float | None = None
    guided_target_valid: bool = False
    guided_target_updated_at: str | None = None

    last_message_at: str | None = None
    last_heartbeat_at: str | None = None

    def to_payload_fields(self) -> dict[str, Any]:
        """Return only fields used by the telemetry protocol."""

        return {
            "armed": self.armed,
            "flight_mode": self.flight_mode,
            "lat": self.lat,
            "lon": self.lon,
            "heading": self.heading,
            "groundspeed": self.groundspeed,
            "battery_v": self.battery_v,
            "battery_remaining_pct": (
                self.battery_remaining_pct
            ),
            "gps_fix": self.gps_fix,
            "gps_fix_label": self.gps_fix_label,
            "gps_hdop": self.gps_hdop,
            "wp_index": self.wp_index,
            "wp_total": self.wp_total,
            "wp_dist": self.wp_dist,
            "mission_loaded": self.mission_loaded,
            "mission_complete": self.mission_complete,
            "guided_target_lat": self.guided_target_lat,
            "guided_target_lon": self.guided_target_lon,
            "guided_target_alt": self.guided_target_alt,
            "guided_target_valid": self.guided_target_valid,
            "guided_target_updated_at": self.guided_target_updated_at,
        }


class TelemetryStore:
    """Thread-safe storage for the latest telemetry snapshot."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot = TelemetrySnapshot()

    def get_snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            return self._snapshot

    def update(self, **changes: Any) -> TelemetrySnapshot:
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                **changes,
            )
            return self._snapshot
