"""Configuration loader for the Raspberry Pi USV onboard application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = PROJECT_ROOT / "config" / ".env"


@dataclass(frozen=True)
class Settings:
    app_env: str
    project_root: Path

    vehicle_id: str

    mavlink_connection: str
    mavlink_heartbeat_timeout: float

    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_keepalive: int
    mqtt_qos: int

    ack_timeout_seconds: float
    log_interval_seconds: float
    sync_batch_size: int

    outbox_db_path: Path
    local_log_dir: Path

    internet_probe_targets: tuple[str, ...]
    internet_probe_interval_seconds: float
    internet_probe_timeout_seconds: float
    internet_success_confirmations: int
    internet_failure_confirmations: int

    # Mission changes are event-driven, with a quiet periodic verification
    # fallback. Identical mission content does not create a new revision.
    mission_refresh_interval_seconds: float = 15.0

    # On Ctrl+C, stop producing new records and allow pending ACK/replay work
    # to drain for a bounded time before closing the session.
    shutdown_drain_timeout_seconds: float = 30.0

    # Request POSITION_TARGET_GLOBAL_INT at 1 Hz for GUIDED target display.
    guided_target_message_interval_seconds: float = 1.0


def load_settings(env_path: Path | None = None) -> Settings:
    """Load and validate onboard configuration."""

    selected_env = env_path or DEFAULT_ENV_PATH

    if not selected_env.exists():
        raise FileNotFoundError(
            f"File konfigurasi tidak ditemukan: {selected_env}"
        )

    load_dotenv(selected_env, override=True)

    settings = Settings(
        app_env=_required_string("APP_ENV"),
        project_root=PROJECT_ROOT,
        vehicle_id=_required_string("VEHICLE_ID"),

        mavlink_connection=_required_string(
            "MAVLINK_CONNECTION"
        ),
        mavlink_heartbeat_timeout=_positive_float(
            "MAVLINK_HEARTBEAT_TIMEOUT"
        ),

        mqtt_host=_required_string("MQTT_HOST"),
        mqtt_port=_port("MQTT_PORT"),
        mqtt_username=_optional_string("MQTT_USERNAME"),
        mqtt_password=_optional_string("MQTT_PASSWORD"),
        mqtt_keepalive=_positive_integer(
            "MQTT_KEEPALIVE"
        ),
        mqtt_qos=_mqtt_qos("MQTT_QOS"),

        ack_timeout_seconds=_positive_float(
            "ACK_TIMEOUT_SECONDS"
        ),
        log_interval_seconds=_positive_float(
            "LOG_INTERVAL_SECONDS"
        ),
        sync_batch_size=_positive_integer(
            "SYNC_BATCH_SIZE"
        ),

        outbox_db_path=_project_path(
            _required_string("OUTBOX_DB_PATH")
        ),
        local_log_dir=_project_path(
            _required_string("LOCAL_LOG_DIR")
        ),

        internet_probe_targets=_csv_strings(
            "INTERNET_PROBE_TARGETS"
        ),
        internet_probe_interval_seconds=_positive_float(
            "INTERNET_PROBE_INTERVAL_SECONDS"
        ),
        internet_probe_timeout_seconds=_positive_float(
            "INTERNET_PROBE_TIMEOUT_SECONDS"
        ),
        internet_success_confirmations=_positive_integer(
            "INTERNET_SUCCESS_CONFIRMATIONS"
        ),
        internet_failure_confirmations=_positive_integer(
            "INTERNET_FAILURE_CONFIRMATIONS"
        ),

        mission_refresh_interval_seconds=_optional_nonnegative_float(
            "MISSION_REFRESH_INTERVAL_SECONDS",
            15.0,
        ),
        shutdown_drain_timeout_seconds=_optional_nonnegative_float(
            "SHUTDOWN_DRAIN_TIMEOUT_SECONDS",
            30.0,
        ),
        guided_target_message_interval_seconds=_optional_positive_float(
            "GUIDED_TARGET_MESSAGE_INTERVAL_SECONDS",
            1.0,
        ),
    )

    _validate_vehicle_id(settings.vehicle_id)

    settings.outbox_db_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    settings.local_log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    return settings


def _required_string(name: str) -> str:
    value = os.getenv(name)

    if value is None or not value.strip():
        raise ValueError(
            f"Konfigurasi wajib belum diisi: {name}"
        )

    return value.strip()


def _optional_string(name: str) -> str | None:
    value = os.getenv(name)

    if value is None:
        return None

    cleaned = value.strip()
    return cleaned or None


def _positive_integer(name: str) -> int:
    raw = _required_string(name)

    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} harus berupa integer"
        ) from exc

    if value <= 0:
        raise ValueError(
            f"{name} harus lebih besar dari 0"
        )

    return value


def _positive_float(name: str) -> float:
    raw = _required_string(name)

    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} harus berupa angka"
        ) from exc

    if value <= 0:
        raise ValueError(
            f"{name} harus lebih besar dari 0"
        )

    return value



def _optional_nonnegative_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return float(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} harus berupa angka") from exc
    if value < 0:
        raise ValueError(f"{name} tidak boleh negatif")
    return value


def _optional_positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return float(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} harus berupa angka") from exc
    if value <= 0:
        raise ValueError(f"{name} harus lebih besar dari 0")
    return value


def _port(name: str) -> int:
    value = _positive_integer(name)

    if value > 65535:
        raise ValueError(
            f"{name} harus berada pada rentang 1-65535"
        )

    return value


def _mqtt_qos(name: str) -> int:
    raw = _required_string(name)

    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} harus berupa integer"
        ) from exc

    if value not in {0, 1, 2}:
        raise ValueError(
            f"{name} hanya boleh bernilai 0, 1, atau 2"
        )

    return value


def _csv_strings(name: str) -> tuple[str, ...]:
    raw = _required_string(name)

    values = tuple(
        item.strip()
        for item in raw.split(",")
        if item.strip()
    )

    if not values:
        raise ValueError(
            f"{name} harus memiliki minimal satu nilai"
        )

    return values


def _project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    return path.resolve()


def _validate_vehicle_id(vehicle_id: str) -> None:
    forbidden = {"/", "+", "#"}

    if any(character in vehicle_id for character in forbidden):
        raise ValueError(
            "VEHICLE_ID tidak boleh mengandung /, +, atau #"
        )
