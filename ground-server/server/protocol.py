"""Shared protocol definitions for the USV supervisory system.

File ini menjadi implementasi Python dari docs/protocol_contract.md.
Definisi di Raspberry Pi USV dan Raspberry Pi Lab harus identik.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


PROTOCOL_NAME = "USV Supervisory Protocol"
PROTOCOL_VERSION = "1.0.0"

DEFAULT_VEHICLE_ID = "usv-01"
DATA_SOURCE_PIXHAWK = "PIXHAWK_MAVLINK"

MQTT_QOS = 1


class SupervisorState(str, Enum):
    NORMAL = "NORMAL"
    GCS_LOST = "GCS_LOST"
    RECOVERY = "RECOVERY"
    PIXHAWK_LOST = "PIXHAWK_LOST"


class APLinkStatus(str, Enum):
    OK = "OK"
    LOST = "LOST"


class InternetStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class MQTTConnectionStatus(str, Enum):
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


class MQTTPublishStatus(str, Enum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class ACKStatus(str, Enum):
    PENDING = "PENDING"
    ACKED = "ACKED"
    TIMEOUT = "TIMEOUT"
    INVALID = "INVALID"


class SyncStatus(str, Enum):
    IDLE = "IDLE"
    SYNCING = "SYNCING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


class DeliveryType(str, Enum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"


TELEMETRY_FIELDS = (
    "protocol_version",
    "vehicle_id",
    "session_id",
    "seq_id",
    "timestamp",
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
)


ACK_FIELDS = (
    "protocol_version",
    "vehicle_id",
    "session_id",
    "seq_id",
    "status",
    "stored_at",
)


RECORD_IDENTITY_FIELDS = (
    "vehicle_id",
    "session_id",
    "seq_id",
)


def telemetry_topic(vehicle_id: str) -> str:
    """Return telemetry topic for one vehicle."""

    return f"usv/{_validated_topic_vehicle_id(vehicle_id)}/telemetry"


def ack_topic(vehicle_id: str) -> str:
    """Return ACK topic for one vehicle."""

    return f"usv/{_validated_topic_vehicle_id(vehicle_id)}/ack"


def status_topic(vehicle_id: str) -> str:
    """Return status topic for one vehicle."""

    return f"usv/{_validated_topic_vehicle_id(vehicle_id)}/status"


def waypoints_topic(vehicle_id: str) -> str:
    """Return waypoint topic for one vehicle."""

    return f"usv/{_validated_topic_vehicle_id(vehicle_id)}/waypoints"


def telemetry_subscription_topic() -> str:
    """Topic wildcard used by the Raspberry Pi Lab backend."""

    return "usv/+/telemetry"


def status_subscription_topic() -> str:
    return "usv/+/status"


def waypoints_subscription_topic() -> str:
    return "usv/+/waypoints"


def record_identity(
    vehicle_id: str,
    session_id: str,
    seq_id: int,
) -> tuple[str, str, int]:
    """Return the database and ACK identity of one record."""

    validate_record_identity(vehicle_id, session_id, seq_id)
    return vehicle_id, session_id, seq_id


def validate_record_identity(
    vehicle_id: Any,
    session_id: Any,
    seq_id: Any,
) -> None:
    """Validate vehicle_id, session_id, and seq_id."""

    if not isinstance(vehicle_id, str) or not vehicle_id.strip():
        raise ValueError("vehicle_id harus berupa string yang tidak kosong")

    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id harus berupa string yang tidak kosong")

    if isinstance(seq_id, bool) or not isinstance(seq_id, int):
        raise ValueError("seq_id harus berupa integer")

    if seq_id < 1:
        raise ValueError("seq_id harus lebih besar atau sama dengan 1")


def validate_telemetry_payload(payload: Mapping[str, Any]) -> None:
    """Validate minimum structure of a telemetry payload."""

    if not isinstance(payload, Mapping):
        raise ValueError("payload telemetry harus berupa mapping/objek JSON")

    missing = [field for field in TELEMETRY_FIELDS if field not in payload]
    if missing:
        raise ValueError(
            "field telemetry tidak lengkap: " + ", ".join(missing)
        )

    if payload["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError(
            "protocol_version tidak didukung: "
            f"{payload['protocol_version']!r}"
        )

    validate_record_identity(
        payload["vehicle_id"],
        payload["session_id"],
        payload["seq_id"],
    )

    _validate_enum_value(
        payload["delivery_type"],
        DeliveryType,
        "delivery_type",
    )
    _validate_enum_value(
        payload["supervisor_state"],
        SupervisorState,
        "supervisor_state",
    )
    _validate_enum_value(
        payload["ap_link"],
        APLinkStatus,
        "ap_link",
    )
    _validate_enum_value(
        payload["internet_status"],
        InternetStatus,
        "internet_status",
    )
    _validate_enum_value(
        payload["mqtt_connection_status"],
        MQTTConnectionStatus,
        "mqtt_connection_status",
    )
    _validate_enum_value(
        payload["mqtt_publish_status"],
        MQTTPublishStatus,
        "mqtt_publish_status",
    )
    _validate_enum_value(
        payload["ack_status"],
        ACKStatus,
        "ack_status",
    )
    _validate_enum_value(
        payload["sync_status"],
        SyncStatus,
        "sync_status",
    )

    _validate_non_negative_integer(
        payload["retry_count"],
        "retry_count",
    )
    _validate_non_negative_integer(
        payload["buffer_count"],
        "buffer_count",
    )


def validate_ack_payload(
    payload: Mapping[str, Any],
    *,
    expected_vehicle_id: str | None = None,
    expected_session_id: str | None = None,
    expected_seq_id: int | None = None,
) -> None:
    """Validate an application ACK from the Raspberry Pi Lab backend."""

    if not isinstance(payload, Mapping):
        raise ValueError("payload ACK harus berupa mapping/objek JSON")

    missing = [field for field in ACK_FIELDS if field not in payload]
    if missing:
        raise ValueError("field ACK tidak lengkap: " + ", ".join(missing))

    if payload["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError(
            "protocol_version ACK tidak didukung: "
            f"{payload['protocol_version']!r}"
        )

    validate_record_identity(
        payload["vehicle_id"],
        payload["session_id"],
        payload["seq_id"],
    )

    if payload["status"] != ACKStatus.ACKED.value:
        raise ValueError("status ACK harus bernilai ACKED")

    if not isinstance(payload["stored_at"], str) or not payload["stored_at"].strip():
        raise ValueError("stored_at ACK harus berupa timestamp string")

    if (
        expected_vehicle_id is not None
        and payload["vehicle_id"] != expected_vehicle_id
    ):
        raise ValueError("vehicle_id ACK tidak sesuai")

    if (
        expected_session_id is not None
        and payload["session_id"] != expected_session_id
    ):
        raise ValueError("session_id ACK tidak sesuai")

    if expected_seq_id is not None and payload["seq_id"] != expected_seq_id:
        raise ValueError("seq_id ACK tidak sesuai")


def _validated_topic_vehicle_id(vehicle_id: str) -> str:
    if not isinstance(vehicle_id, str) or not vehicle_id.strip():
        raise ValueError("vehicle_id topic tidak boleh kosong")

    cleaned = vehicle_id.strip()

    if "/" in cleaned or "+" in cleaned or "#" in cleaned:
        raise ValueError(
            "vehicle_id tidak boleh mengandung karakter topic MQTT /, +, atau #"
        )

    return cleaned


def _validate_enum_value(
    value: Any,
    enum_class: type[Enum],
    field_name: str,
) -> None:
    allowed = {item.value for item in enum_class}

    if value not in allowed:
        raise ValueError(
            f"{field_name} tidak valid: {value!r}; "
            f"nilai yang diperbolehkan: {sorted(allowed)}"
        )


def _validate_non_negative_integer(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} harus berupa integer")

    if value < 0:
        raise ValueError(f"{field_name} tidak boleh bernilai negatif")
