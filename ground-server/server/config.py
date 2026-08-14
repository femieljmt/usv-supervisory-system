"""Cross-platform configuration loader for the USV ground server."""

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

    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_keepalive: int
    mqtt_qos: int

    database_path: Path

    dashboard_host: str
    dashboard_port: int
    dashboard_refresh_seconds: float
    dashboard_track_limit: int
    dashboard_live_seconds: float
    dashboard_stale_seconds: float

    terminal_summary_interval: int


def load_settings(env_path: Path | None = None) -> Settings:
    """Load and validate server configuration."""

    selected_env = env_path or DEFAULT_ENV_PATH

    if not selected_env.exists():
        raise FileNotFoundError(
            f"File konfigurasi tidak ditemukan: {selected_env}"
        )

    load_dotenv(selected_env, override=True)

    settings = Settings(
        app_env=_required_string("APP_ENV"),
        project_root=PROJECT_ROOT,
        mqtt_host=_required_string("MQTT_HOST"),
        mqtt_port=_port("MQTT_PORT"),
        mqtt_username=_optional_string("MQTT_USERNAME"),
        mqtt_password=_optional_string("MQTT_PASSWORD"),
        mqtt_keepalive=_positive_integer("MQTT_KEEPALIVE"),
        mqtt_qos=_mqtt_qos("MQTT_QOS"),
        database_path=_project_path(_required_string("DATABASE_PATH")),
        dashboard_host=_required_string("DASHBOARD_HOST"),
        dashboard_port=_port("DASHBOARD_PORT"),
        dashboard_refresh_seconds=_positive_float(
            "DASHBOARD_REFRESH_SECONDS",
            default=2.0,
        ),
        dashboard_track_limit=_positive_integer(
            "DASHBOARD_TRACK_LIMIT",
            default=300,
        ),
        dashboard_live_seconds=_positive_float(
            "DASHBOARD_LIVE_SECONDS",
            default=5.0,
        ),
        dashboard_stale_seconds=_positive_float(
            "DASHBOARD_STALE_SECONDS",
            default=15.0,
        ),
        terminal_summary_interval=_positive_integer(
            "TERMINAL_SUMMARY_INTERVAL",
            default=10,
        ),
    )

    if settings.dashboard_stale_seconds <= settings.dashboard_live_seconds:
        raise ValueError(
            "DASHBOARD_STALE_SECONDS harus lebih besar dari DASHBOARD_LIVE_SECONDS"
        )

    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    return settings


def _required_string(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"Konfigurasi wajib belum diisi: {name}")
    return value.strip()


def _optional_string(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _positive_integer(name: str, *, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            raise ValueError(f"Konfigurasi wajib belum diisi: {name}")
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} harus berupa integer") from exc
    if value <= 0:
        raise ValueError(f"{name} harus lebih besar dari 0")
    return value


def _positive_float(name: str, *, default: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            raise ValueError(f"Konfigurasi wajib belum diisi: {name}")
        return default
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
        raise ValueError(f"{name} harus berada pada rentang 1-65535")
    return value


def _mqtt_qos(name: str) -> int:
    raw = _required_string(name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} harus berupa integer") from exc
    if value not in {0, 1, 2}:
        raise ValueError(f"{name} hanya boleh bernilai 0, 1, atau 2")
    return value


def _project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()
