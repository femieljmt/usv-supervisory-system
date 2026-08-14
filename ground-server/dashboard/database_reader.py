"""Read-only aggregation and evidence export for the USV dashboard."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Callable

from server.database import TelemetryDatabase
from server.mission_database import MissionDatabase


EXPORT_FIELDS = (
    "record_timestamp",
    "backend_first_received_at",
    "backend_last_received_at",
    "vehicle_id",
    "session_id",
    "seq_id",
    "data_source",
    "delivery_type",
    "supervisor_state",
    "mission_status",
    "ap_link",
    "internet_status",
    "mqtt_connection_status",
    "mqtt_publish_status",
    "ack_status",
    "ack_latency_ms",
    "retry_count",
    "buffer_count",
    "sync_status",
    "armed",
    "flight_mode",
    "lat",
    "lon",
    "heading",
    "groundspeed",
    "battery_v",
    "battery_remaining_pct",
    "gps_fix",
    "gps_fix_label",
    "gps_hdop",
    "wp_index",
    "wp_total",
    "wp_dist",
    "mission_loaded",
    "mission_complete",
    "receive_count",
    "duplicate_received",
    "ack_send_count",
)


class DashboardDataReader:
    def __init__(
        self,
        telemetry_database: TelemetryDatabase,
        mission_database: MissionDatabase,
        *,
        live_seconds: float = 5.0,
        stale_seconds: float = 15.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if live_seconds <= 0:
            raise ValueError("live_seconds harus lebih besar dari 0")
        if stale_seconds <= live_seconds:
            raise ValueError("stale_seconds harus lebih besar dari live_seconds")
        self._telemetry = telemetry_database
        self._mission = mission_database
        self._live_seconds = float(live_seconds)
        self._stale_seconds = float(stale_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def list_vehicles(self) -> list[str]:
        vehicles = list(self._telemetry.list_vehicles())
        for vehicle in self._mission.list_latest_vehicles():
            if vehicle not in vehicles:
                vehicles.append(vehicle)
        return vehicles

    def list_sessions(self, vehicle_id: str) -> list[dict[str, Any]]:
        sessions = self._telemetry.list_sessions(vehicle_id)
        ranked: list[dict[str, Any]] = []
        for index, item in enumerate(sessions, start=1):
            value = dict(item)
            value["rank"] = index
            value["is_latest"] = index == 1
            value["session_started_at"] = value.get("first_record_at")
            value["session_ended_at"] = value.get("last_record_at")
            ranked.append(value)
        return ranked

    def snapshot(
        self,
        *,
        vehicle_id: str | None = None,
        session_id: str | None = None,
        track_limit: int = 500,
        track_mode: str = "main",
    ) -> dict[str, Any]:
        vehicles = self.list_vehicles()
        latest_global = self._telemetry.get_latest_telemetry()
        selected_vehicle = vehicle_id
        if selected_vehicle not in vehicles:
            selected_vehicle = (
                str(latest_global["vehicle_id"])
                if latest_global is not None
                else (vehicles[0] if vehicles else None)
            )

        if selected_vehicle is None:
            return self._empty_snapshot(vehicles)

        sessions = self.list_sessions(selected_vehicle)
        session_ids = {str(item["session_id"]) for item in sessions}
        selected_session = session_id if session_id in session_ids else None
        if selected_session is None and sessions:
            selected_session = str(sessions[0]["session_id"])

        selected_session_meta = next(
            (item for item in sessions if str(item["session_id"]) == selected_session),
            None,
        )
        selected_session_is_latest = bool(
            selected_session_meta and selected_session_meta.get("is_latest")
        )

        if selected_session is None:
            return {
                **self._empty_snapshot(vehicles),
                "vehicle_id": selected_vehicle,
                "sessions": [],
                "mission": self._mission.get_latest(selected_vehicle),
            }

        records = self._telemetry.get_session_dashboard_records(
            selected_vehicle,
            selected_session,
        )
        latest = self._telemetry.get_latest_telemetry(
            selected_vehicle,
            session_id=selected_session,
        )
        summary = self._telemetry.get_session_summary(
            selected_vehicle,
            selected_session,
        )
        sequence = _sequence_integrity(
            self._telemetry.get_session_sequence_ids(
                selected_vehicle,
                selected_session,
            )
        )
        state_distribution = {
            "NORMAL": int(summary.get("normal_records") or 0),
            "GCS_LOST": int(summary.get("gcs_lost_records") or 0),
            "RECOVERY": int(summary.get("recovery_records") or 0),
            "PIXHAWK_LOST": int(summary.get("pixhawk_lost_records") or 0),
        }
        sources = sorted(
            source.strip()
            for source in str(summary.get("data_sources_csv") or "UNKNOWN").split(",")
            if source.strip()
        )

        summary.update(sequence)
        summary["state_distribution"] = state_distribution
        summary["data_sources"] = sources
        summary["session_id"] = selected_session
        summary["vehicle_id"] = selected_vehicle
        if latest:
            summary.update(
                {
                    "final_state": latest.get("supervisor_state"),
                    "final_buffer_count": latest.get("buffer_count"),
                    "final_sync_status": latest.get("sync_status"),
                    "final_ack_status": latest.get("ack_status"),
                }
            )

        raw_coordinate_records = [
            record
            for record in records
            if record.get("lat") is not None or record.get("lon") is not None
        ]
        valid_track_records = [
            record for record in raw_coordinate_records if _gps_record_valid(record)
        ]
        rejected_track_points = len(raw_coordinate_records) - len(valid_track_records)
        normalized_track_mode = str(track_mode or "main").strip().lower()
        if normalized_track_mode not in {"main", "recent"}:
            normalized_track_mode = "main"
        safe_track_limit = min(max(1, int(track_limit)), 5000)
        if normalized_track_mode == "recent":
            track_records = valid_track_records[-safe_track_limit:]
        else:
            track_records = _downsample(valid_track_records, safe_track_limit)
        track = [
            {
                "timestamp": item["record_timestamp"],
                "received_at": item["backend_last_received_at"],
                "seq_id": item["seq_id"],
                "lat": item["lat"],
                "lon": item["lon"],
                "state": item["supervisor_state"],
                "delivery_type": item["delivery_type"],
                "speed": item["groundspeed"],
                "heading": item["heading"],
                "gps_fix": item.get("gps_fix"),
                "gps_fix_label": item.get("gps_fix_label"),
                "gps_hdop": item.get("gps_hdop"),
                "wp_index": item.get("wp_index"),
                "wp_total": item.get("wp_total"),
                "flight_mode": item.get("flight_mode"),
                "armed": item.get("armed"),
                "battery_v": item.get("battery_v"),
            }
            for item in track_records
        ]
        gps_quality = _gps_quality_snapshot(
            latest_record=latest,
            valid_track_points=len(valid_track_records),
            rejected_track_points=rejected_track_points,
        )
        guided_target = _guided_target_snapshot(latest)
        manual_segment = _latest_mode_segment(
            valid_track_records,
            mode="MANUAL",
            maximum_points=max(1, int(track_limit)),
        )

        chart_records = [
            _chart_record(record)
            for record in _downsample(records, 600)
        ]
        evidence_records = [
            _evidence_record(record)
            for record in records
            if (
                record.get("delivery_type") == "REPLAY"
                or record.get("supervisor_state") in {"GCS_LOST", "RECOVERY"}
                or int(record.get("buffer_count") or 0) > 0
                or int(record.get("retry_count") or 0) > 0
            )
        ][-120:]

        mission = self._mission.get_for_session(selected_vehicle, selected_session)
        latest_vehicle_mission = self._mission.get_latest(selected_vehicle)
        if mission is not None:
            mission = dict(mission)
            mission["session_match"] = True
            mission["revision_key"] = (
                mission.get("payload_hash")
                or f"{mission.get('revision', 0)}:{mission.get('generated_at', '')}"
            )
            generated = _parse_timestamp(mission.get("generated_at"))
            received = _parse_timestamp(mission.get("received_at"))
            mission_skew = (
                (received - generated).total_seconds()
                if generated is not None and received is not None
                else None
            )
            mission["display_at"] = (
                mission.get("generated_at") or mission.get("received_at")
            )
            mission["clock_skew_detected"] = bool(
                mission_skew is not None and abs(mission_skew) > 300.0
            )
            mission["clock_skew_seconds"] = (
                None if mission_skew is None else round(mission_skew, 3)
            )
        mission_availability = (
            "SESSION" if mission is not None
            else "MISSING_FOR_SESSION" if latest_vehicle_mission is not None
            else "NO_MISSION"
        )

        return {
            "vehicle_id": selected_vehicle,
            "session_id": selected_session,
            "session_rank": selected_session_meta.get("rank") if selected_session_meta else None,
            "session_is_latest": selected_session_is_latest,
            "vehicles": vehicles,
            "sessions": sessions,
            "latest": latest,
            "freshness": self._freshness(
                latest,
                is_latest_session=selected_session_is_latest,
            ),
            "summary": summary,
            "track": track,
            "track_meta": {
                "mode": normalized_track_mode,
                "limit": safe_track_limit,
                "returned_points": len(track),
                "total_valid_points": len(valid_track_records),
                "truncated": len(track) < len(valid_track_records),
            },
            "gps_quality": gps_quality,
            "guided_target": guided_target,
            "manual_segment": manual_segment,
            "chart_records": chart_records,
            "store_forward_records": evidence_records,
            "state_history": self._telemetry.get_state_history(
                selected_vehicle,
                session_id=selected_session,
                limit=80,
            ),
            "mission": mission,
            "mission_availability": mission_availability,
            "latest_vehicle_mission_session_id": (
                latest_vehicle_mission.get("session_id")
                if latest_vehicle_mission is not None else None
            ),
            "supporting_sensors": _supporting_sensor_snapshot(chart_records),
        }

    def playback_track(
        self,
        *,
        vehicle_id: str,
        session_id: str,
        limit: int = 5000,
    ) -> dict[str, Any]:
        """Return a session-scoped GPS playback series on demand."""

        records = self._telemetry.get_session_track_records(vehicle_id, session_id)
        valid = [record for record in records if _gps_record_valid(record)]
        safe_limit = min(max(int(limit), 1), 10000)
        sampled = _downsample(valid, safe_limit)
        points = [
            {
                "timestamp": item.get("record_timestamp"),
                "received_at": item.get("backend_last_received_at"),
                "seq_id": item.get("seq_id"),
                "lat": item.get("lat"),
                "lon": item.get("lon"),
                "state": item.get("supervisor_state"),
                "delivery_type": item.get("delivery_type"),
                "speed": item.get("groundspeed"),
                "heading": item.get("heading"),
                "gps_fix": item.get("gps_fix"),
                "gps_fix_label": item.get("gps_fix_label"),
                "gps_hdop": item.get("gps_hdop"),
                "wp_index": item.get("wp_index"),
                "wp_total": item.get("wp_total"),
                "flight_mode": item.get("flight_mode"),
                "armed": item.get("armed"),
                "battery_v": item.get("battery_v"),
            }
            for item in sampled
        ]
        return {
            "vehicle_id": vehicle_id,
            "session_id": session_id,
            "points": points,
            "returned_points": len(points),
            "total_valid_points": len(valid),
            "sampled": len(points) < len(valid),
        }

    def export_session_csv(self, *, vehicle_id: str, session_id: str) -> bytes:
        records = self._telemetry.get_session_records(vehicle_id, session_id)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in EXPORT_FIELDS})
        return output.getvalue().encode("utf-8-sig")

    def _freshness(
        self,
        latest: dict[str, Any] | None,
        *,
        is_latest_session: bool = True,
    ) -> dict[str, Any]:
        if latest is None:
            return {
                "status": "NO_DATA",
                "age_seconds": None,
                "telemetry_at": None,
                "server_received_at": None,
                "reference_at": None,
                "timestamp_source": None,
                "clock_skew_detected": False,
                "clock_skew_seconds": None,
                "last_received_at": None,
                "live_threshold_seconds": self._live_seconds,
                "stale_threshold_seconds": self._stale_seconds,
            }

        telemetry_at_raw = latest.get("record_timestamp")
        server_received_raw = latest.get("backend_last_received_at")
        telemetry_at = _parse_timestamp(telemetry_at_raw)
        server_received = _parse_timestamp(server_received_raw)

        clock_skew_seconds: float | None = None
        clock_skew_detected = False
        if telemetry_at is not None and server_received is not None:
            clock_skew_seconds = (server_received - telemetry_at).total_seconds()
            # A server cannot receive a record several minutes before it was
            # produced. This is the exact failure found in the uploaded DB:
            # 20 July telemetry was stamped as received on 14 July.
            clock_skew_detected = abs(clock_skew_seconds) > 300.0

        use_telemetry_clock = bool(
            telemetry_at is not None
            and (server_received is None or (clock_skew_seconds or 0.0) < -300.0)
        )
        reference = telemetry_at if use_telemetry_clock else server_received
        reference_raw = telemetry_at_raw if use_telemetry_clock else server_received_raw
        timestamp_source = "RECORD_TIMESTAMP" if use_telemetry_clock else "SERVER_RECEIPT"

        if not is_latest_session:
            status = "HISTORICAL"
            age = None
        elif reference is None:
            status = "UNKNOWN"
            age = None
        else:
            now = self._clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            age = max(0.0, (now.astimezone(timezone.utc) - reference).total_seconds())
            if age <= self._live_seconds:
                status = "LIVE"
            elif age <= self._stale_seconds:
                status = "STALE"
            else:
                status = "OFFLINE"

        return {
            "status": status,
            "age_seconds": None if age is None else round(age, 3),
            "telemetry_at": telemetry_at_raw,
            "server_received_at": server_received_raw,
            "reference_at": reference_raw,
            "timestamp_source": timestamp_source,
            "clock_skew_detected": clock_skew_detected,
            "clock_skew_seconds": (
                None if clock_skew_seconds is None else round(clock_skew_seconds, 3)
            ),
            # Compatibility field used by older dashboard code. It now means
            # the chosen freshness reference rather than always server receipt.
            "last_received_at": reference_raw,
            "live_threshold_seconds": self._live_seconds,
            "stale_threshold_seconds": self._stale_seconds,
        }

    @staticmethod
    def _empty_snapshot(vehicles: list[str]) -> dict[str, Any]:
        return {
            "vehicle_id": None,
            "session_id": None,
            "session_rank": None,
            "session_is_latest": False,
            "vehicles": vehicles,
            "sessions": [],
            "latest": None,
            "freshness": {
                "status": "NO_DATA",
                "age_seconds": None,
                "last_received_at": None,
                "telemetry_at": None,
                "server_received_at": None,
                "reference_at": None,
                "clock_skew_detected": False,
            },
            "summary": {},
            "track": [],
            "track_meta": {
                "mode": "main",
                "limit": 0,
                "returned_points": 0,
                "total_valid_points": 0,
                "truncated": False,
            },
            "gps_quality": {
                "status": "NO_DATA",
                "fix": None,
                "fix_label": None,
                "hdop": None,
                "valid_track_points": 0,
                "rejected_track_points": 0,
            },
            "guided_target": None,
            "manual_segment": {
                "mode": "MANUAL",
                "active": False,
                "points": [],
                "start": None,
                "end": None,
            },
            "chart_records": [],
            "store_forward_records": [],
            "state_history": [],
            "mission": None,
            "mission_availability": "NO_MISSION",
            "latest_vehicle_mission_session_id": None,
            "supporting_sensors": {
                "status": "NO_DATA",
                "source": None,
                "latest": None,
                "records": [],
            },
        }


def _guided_target_snapshot(
    latest_record: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if latest_record is None:
        return None
    if str(latest_record.get("flight_mode") or "").upper() != "GUIDED":
        return None

    payload = _payload_object(latest_record.get("payload_json"))
    if not bool(payload.get("guided_target_valid")):
        return None

    lat = _optional_number(payload.get("guided_target_lat"))
    lon = _optional_number(payload.get("guided_target_lon"))
    if lat is None or lon is None:
        return None
    if not (-85.05112878 <= lat <= 85.05112878 and -180.0 <= lon <= 180.0):
        return None
    if abs(lat) < 1e-12 and abs(lon) < 1e-12:
        return None

    return {
        "lat": lat,
        "lon": lon,
        "alt": _optional_number(payload.get("guided_target_alt")),
        "updated_at": payload.get("guided_target_updated_at"),
        "source": "POSITION_TARGET_GLOBAL_INT",
    }


def _latest_mode_segment(
    records: list[dict[str, Any]],
    *,
    mode: str,
    maximum_points: int,
) -> dict[str, Any]:
    target_mode = mode.upper()
    segments: list[list[dict[str, Any]]] = []
    active: list[dict[str, Any]] = []

    for record in records:
        record_mode = str(record.get("flight_mode") or "").upper()
        if record_mode == target_mode:
            active.append(record)
        elif active:
            segments.append(active)
            active = []

    if active:
        segments.append(active)

    if not segments:
        return {
            "mode": target_mode,
            "active": False,
            "points": [],
            "start": None,
            "end": None,
        }

    latest_segment = segments[-1]
    latest_mode = str(records[-1].get("flight_mode") or "").upper() if records else ""
    sampled = _downsample(latest_segment, maximum_points)

    def point(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "timestamp": record.get("record_timestamp"),
            "seq_id": record.get("seq_id"),
            "lat": record.get("lat"),
            "lon": record.get("lon"),
            "heading": record.get("heading"),
            "speed": record.get("groundspeed"),
        }

    points = [point(record) for record in sampled]
    return {
        "mode": target_mode,
        "active": latest_mode == target_mode,
        "points": points,
        "start": point(latest_segment[0]),
        "end": point(latest_segment[-1]),
        "started_at": latest_segment[0].get("record_timestamp"),
        "ended_at": None if latest_mode == target_mode else latest_segment[-1].get("record_timestamp"),
    }


def _gps_record_valid(record: dict[str, Any]) -> bool:
    lat = _optional_number(record.get("lat"))
    lon = _optional_number(record.get("lon"))
    if lat is None or lon is None:
        return False
    if not (-85.05112878 <= lat <= 85.05112878 and -180.0 <= lon <= 180.0):
        return False
    if abs(lat) < 1e-12 and abs(lon) < 1e-12:
        return False
    fix = _optional_number(record.get("gps_fix"))
    if fix is not None and fix < 2:
        return False
    return True


def _gps_quality_snapshot(
    *,
    latest_record: dict[str, Any] | None,
    valid_track_points: int,
    rejected_track_points: int,
) -> dict[str, Any]:
    if latest_record is None:
        return {
            "status": "NO_DATA",
            "fix": None,
            "fix_label": None,
            "hdop": None,
            "valid_track_points": valid_track_points,
            "rejected_track_points": rejected_track_points,
        }

    fix_value = _optional_number(latest_record.get("gps_fix"))
    fix = int(fix_value) if fix_value is not None else None
    label = latest_record.get("gps_fix_label")
    hdop = _optional_number(latest_record.get("gps_hdop"))
    lat = _optional_number(latest_record.get("lat"))
    lon = _optional_number(latest_record.get("lon"))
    has_coordinate = (
        lat is not None
        and lon is not None
        and not (abs(lat) < 1e-12 and abs(lon) < 1e-12)
    )

    if not has_coordinate or (fix is not None and fix < 2):
        status = "NO_FIX"
    elif fix is None:
        status = "UNKNOWN"
    elif fix == 2 or (hdop is not None and hdop > 2.5):
        status = "DEGRADED"
    else:
        status = "GOOD"

    return {
        "status": status,
        "fix": fix,
        "fix_label": label,
        "hdop": hdop,
        "valid_track_points": valid_track_points,
        "rejected_track_points": rejected_track_points,
    }


def _chart_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = _payload_object(record.get("payload_json"))
    return {
        "timestamp": record.get("record_timestamp"),
        "received_at": record.get("backend_last_received_at"),
        "seq_id": record.get("seq_id"),
        "state": record.get("supervisor_state"),
        "delivery_type": record.get("delivery_type"),
        "buffer_count": record.get("buffer_count"),
        "retry_count": record.get("retry_count"),
        "ack_latency_ms": record.get("ack_latency_ms"),
        "speed": record.get("groundspeed"),
        "battery_v": record.get("battery_v"),
        "internet_status": record.get("internet_status"),
        "mqtt_status": record.get("mqtt_connection_status"),
        "ack_status": record.get("ack_status"),
        "sync_status": record.get("sync_status"),
        "water_temperature_c": _optional_number(payload.get("water_temperature_c")),
        "water_ph": _optional_number(payload.get("water_ph")),
        "water_turbidity_ntu": _optional_number(payload.get("water_turbidity_ntu")),
        "water_tds_ppm": _optional_number(payload.get("water_tds_ppm")),
        "water_dissolved_oxygen_mg_l": _optional_number(payload.get("water_dissolved_oxygen_mg_l")),
        "water_conductivity_us_cm": _optional_number(payload.get("water_conductivity_us_cm")),
        "solar_pv_voltage_v": _optional_number(payload.get("solar_pv_voltage_v")),
        "solar_pv_current_a": _optional_number(payload.get("solar_pv_current_a")),
        "solar_pv_power_w": _optional_number(payload.get("solar_pv_power_w")),
        "solar_battery_voltage_v": _optional_number(payload.get("solar_battery_voltage_v")),
        "solar_charge_current_a": _optional_number(payload.get("solar_charge_current_a")),
        "solar_state_of_charge_pct": _optional_number(payload.get("solar_state_of_charge_pct")),
        "supporting_data_source": payload.get("supporting_data_source"),
    }


def _payload_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _supporting_sensor_snapshot(records: list[dict[str, Any]]) -> dict[str, Any]:
    sensor_fields = (
        "water_temperature_c",
        "water_ph",
        "water_turbidity_ntu",
        "water_tds_ppm",
        "water_dissolved_oxygen_mg_l",
        "water_conductivity_us_cm",
        "solar_pv_voltage_v",
        "solar_pv_current_a",
        "solar_pv_power_w",
        "solar_battery_voltage_v",
        "solar_charge_current_a",
        "solar_state_of_charge_pct",
    )
    available = [
        record for record in records
        if any(record.get(field) is not None for field in sensor_fields)
    ]
    if not available:
        return {
            "status": "NO_DATA",
            "source": None,
            "latest": None,
            "records": [],
        }
    latest = available[-1]
    source = latest.get("supporting_data_source") or "DATABASE_PAYLOAD"
    return {
        "status": "DATA",
        "source": source,
        "latest": {field: latest.get(field) for field in sensor_fields},
        "records": available,
    }


def _evidence_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": record.get("record_timestamp"),
        "seq_id": record.get("seq_id"),
        "state": record.get("supervisor_state"),
        "delivery_type": record.get("delivery_type"),
        "ack_status": record.get("ack_status"),
        "buffer_count": record.get("buffer_count"),
        "retry_count": record.get("retry_count"),
        "sync_status": record.get("sync_status"),
    }


def _sequence_integrity(sequence_ids: list[int]) -> dict[str, Any]:
    sequences = sorted(set(int(value) for value in sequence_ids))
    if not sequences:
        return {
            "expected_records": 0,
            "missing_records": 0,
            "missing_sequences": [],
            "sequence_contiguous": True,
        }
    expected = sequences[-1] - sequences[0] + 1
    present = set(sequences)
    missing = [value for value in range(sequences[0], sequences[-1] + 1) if value not in present]
    return {
        "expected_records": expected,
        "missing_records": len(missing),
        "missing_sequences": missing[:200],
        "sequence_contiguous": not missing,
    }


def _downsample(items: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    if len(items) <= maximum:
        return items
    if maximum <= 2:
        return [items[0], items[-1]][:maximum]
    step = (len(items) - 1) / (maximum - 1)
    indexes = {round(index * step) for index in range(maximum)}
    indexes.update({0, len(items) - 1})
    return [items[index] for index in sorted(indexes)]


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
