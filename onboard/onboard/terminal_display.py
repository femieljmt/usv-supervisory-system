"""Readable running-log presentation for USV supervisory operation.

The terminal is a live operational monitor, not the research data store.
All protocol fields remain in the daily CSV, persistent outbox, and
supervisory_runtime.log.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


TERMINAL_LOGGER_NAME = "usv.terminal"


def terminal_banner(
    vehicle_id: str,
    mqtt_host: str,
    mqtt_port: int,
    *,
    session_id: str | None = None,
    batch_size: int | None = None,
    session_directory: Path | str | None = None,
    operation_file: Path | str | None = None,
    runtime_file: Path | str | None = None,
    mission_file: Path | str | None = None,
    session_info_file: Path | str | None = None,
    operation_log: Path | str | None = None,
    outbox_path: Path | str | None = None,
    pending_outbox: int | None = None,
) -> str:
    """Return the one-time onboard terminal header and storage paths."""

    # ``operation_log`` is retained only for compatibility with older calls.
    if operation_file is None and operation_log is not None:
        operation_file = operation_log

    lines = [
        "",
        "USV SUPERVISORY OPERATION MONITOR",
        f"Vehicle   : {vehicle_id}",
    ]

    if session_id:
        lines.append(f"Session   : {session_id}")

    lines.extend(
        [
            f"Broker    : {mqtt_host}:{mqtt_port}",
            (
                f"Batch     : {batch_size} records"
                if batch_size is not None
                else "Batch     : -"
            ),
        ]
    )

    if session_directory is not None:
        lines.extend(
            [
                "",
                "SESSION FILES",
                f"Directory : {session_directory}",
            ]
        )
    elif any(
        value is not None
        for value in (
            operation_file,
            runtime_file,
            mission_file,
            session_info_file,
        )
    ):
        lines.extend(["", "SESSION FILES"])

    if operation_file is not None:
        lines.append(f"Operation : {operation_file}")
    if runtime_file is not None:
        lines.append(f"Runtime   : {runtime_file}")
    if mission_file is not None:
        lines.append(f"Mission   : {mission_file}")
    if session_info_file is not None:
        lines.append(f"Info      : {session_info_file}")

    if outbox_path is not None:
        lines.extend(
            [
                "",
                "PERSISTENT STORAGE",
                f"Outbox    : {outbox_path}",
            ]
        )
        if pending_outbox is not None:
            lines.append(
                f"Pending   : {int(pending_outbox)} records"
            )

    lines.extend(
        [
            "",
            (
                "Format    : time [level] [state/event] sequence and "
                "operational status"
            ),
            "-" * 150,
        ]
    )
    return "\n".join(lines)


def operation_table_header() -> str:
    """Compatibility helper describing the running-log fields."""

    return (
        "TIME [LEVEL] [STATE] seq | ARM MODE | AP NET MQTT | "
        "LOG BUF SYNC | WP DIST | POSITION | HDG SPD | BAT | GPS HDOP"
    )


def format_terminal_record(
    record: Mapping[str, Any],
    *,
    backlog_count: int,
    log_status: str = "OK",
    display_sync_status: str | None = None,
    **_: Any,
) -> str:
    """Format one concise operation line in the reference log style."""

    timestamp = _format_time(record.get("timestamp"))
    state = _limited_text(
        record.get("supervisor_state"),
        width=10,
        fallback="UNKNOWN",
    )
    seq_id = _safe_int(record.get("seq_id"), default=0)
    arm = "ARM" if bool(record.get("armed")) else "DIS"
    mode = _limited_text(
        record.get("flight_mode"),
        width=10,
        fallback="UNKNOWN",
    )

    ap = _map_status(
        record.get("ap_link"),
        {"OK": "OK", "LOST": "LOST"},
    )
    net = _map_status(
        record.get("internet_status"),
        {
            "AVAILABLE": "UP",
            "UNAVAILABLE": "DOWN",
            "UNKNOWN": "?",
        },
    )
    mqtt = _map_status(
        record.get("mqtt_connection_status"),
        {
            "CONNECTED": "UP",
            "CONNECTING": "...",
            "DISCONNECTED": "DOWN",
        },
    )

    # When the internet is confirmed unavailable, the MQTT socket status is
    # no longer meaningful to an operator even if its keepalive has not yet
    # expired.
    if net == "DOWN":
        mqtt = "N/A"

    sync = (
        display_sync_status
        if display_sync_status is not None
        else _map_status(
            record.get("sync_status"),
            {
                "IDLE": "IDLE",
                "SYNCING": "SYNCING",
                "SYNCED": "IDLE",
                "FAILED": "WAITING",
            },
        )
    )

    wp = _format_waypoint_pair(
        record.get("wp_index"),
        record.get("wp_total"),
    )
    distance = _format_number(
        record.get("wp_dist"),
        decimals=1,
        suffix="m",
    )
    latitude = _format_coordinate(record.get("lat"))
    longitude = _format_coordinate(record.get("lon"))
    heading = _format_number(
        record.get("heading"),
        decimals=1,
        suffix="°",
    )
    speed = _format_number(
        record.get("groundspeed"),
        decimals=1,
        suffix="m/s",
    )
    battery = _format_number(
        record.get("battery_v"),
        decimals=1,
        suffix="V",
    )
    gps = _format_gps(record.get("gps_fix_label"))
    hdop = _format_number(
        record.get("gps_hdop"),
        decimals=2,
        suffix="",
    )
    buffer_count = max(0, _safe_int(backlog_count, default=0))

    return (
        f"{timestamp} [INFO] [{state:<10}] "
        f"seq={seq_id:05d} | {arm:<3} {mode:<10} | "
        f"ap={ap:<4} net={net:<4} mqtt={mqtt:<4} | "
        f"log={log_status:<4} buf={buffer_count:<4} sync={sync:<7} | "
        f"wp={wp:<7} dist={distance:<7} | "
        f"lat={latitude:<11} lon={longitude:<12} | "
        f"hdg={heading:<7} spd={speed:<8} | "
        f"bat={battery:<7} | gps={gps:<5} hdop={hdop}"
    )


def format_terminal_event(event: Any) -> str:
    """Format a structured terminal event."""

    occurred_at = getattr(event, "occurred_at", datetime.now())
    timestamp = occurred_at.astimezone().strftime("%H:%M:%S")
    level = str(getattr(event, "level", "INFO")).upper()[:5]
    category = str(getattr(event, "category", "SYSTEM")).upper()[:10]
    message = str(getattr(event, "message", ""))
    return f"{timestamp} [{level:<5}] [{category:<10}] {message}"


def format_mission_plan(plan: Any, *, is_update: bool = False) -> str:
    """Format a complete mission plan after initial load or replacement."""

    if not plan.complete:
        error = plan.error or "Mission belum diterima"
        return "\n".join(
            [
                "",
                "MISSION PLAN",
                "STATUS : NOT AVAILABLE",
                f"ERROR  : {error}",
                "",
            ]
        )

    event_time = _format_time(getattr(plan, "downloaded_at", None))
    event_label = "MISSION BARU" if is_update else "MISSION AWAL"
    lines = [
        "",
        (
            f"{event_time} [INFO] [WAYPOINT  ] {event_label} diterima"
            f" | total_items={plan.total_count}"
            f" | executable={plan.executable_count}"
            f" | revision={plan.revision}"
        ),
        (
            "MISSION PLAN RECEIVED"
            f" | revision={plan.revision}"
            + (
                " | waypoint lama diganti oleh daftar aktif ini"
                if is_update
                else " | daftar waypoint aktif pertama"
            )
        ),
        (
            "SEQ | ROLE     | COMMAND                  | "
            "LATITUDE    | LONGITUDE    | ALT      | AUTO"
        ),
        "-" * 94,
    ]

    if not plan.items:
        lines.append(
            "--  | EMPTY    | NO MISSION ITEMS         | "
            "--          | --           | --       | --"
        )

    for item in plan.items:
        role = "HOME" if item.is_home else "MISSION"
        command = item.command_name.replace("MAV_CMD_", "")[:24]
        latitude = (
            f"{item.lat:.7f}" if item.lat is not None else "--"
        )
        longitude = (
            f"{item.lon:.7f}" if item.lon is not None else "--"
        )
        altitude = (
            f"{item.alt:.1f}m" if item.alt is not None else "--"
        )
        autocontinue = "YES" if item.autocontinue else "NO"

        lines.append(
            f"{item.seq:>3} | {role:<8} | {command:<24} | "
            f"{latitude:<11} | {longitude:<12} | "
            f"{altitude:>8} | {autocontinue:<3}"
        )

    lines.extend(["-" * 94, ""])
    return "\n".join(lines)


def _format_time(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "--:--:--"

    cleaned = value.strip()
    iso_value = (
        cleaned[:-1] + "+00:00" if cleaned.endswith("Z") else cleaned
    )
    try:
        parsed = datetime.fromisoformat(iso_value).astimezone()
    except ValueError:
        return "--:--:--"
    return parsed.strftime("%H:%M:%S")


def _format_coordinate(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return "--"


def _format_number(
    value: Any,
    *,
    decimals: int,
    suffix: str,
) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def _format_gps(value: Any) -> str:
    mapping = {
        "NO_GPS": "NONE",
        "NO_FIX": "NOFIX",
        "2D_FIX": "2D",
        "3D_FIX": "3D",
        "DGPS": "DGPS",
        "RTK_FLOAT": "RTKF",
        "RTK_FIXED": "RTK",
        "UNKNOWN": "?",
    }
    if value is None:
        return "?"
    return mapping.get(str(value), str(value)[:5])


def _format_waypoint_pair(current: Any, total: Any) -> str:
    current_value = _safe_int(current, default=0)
    total_value = _safe_int(total, default=0)
    return "-" if total_value <= 0 else f"{current_value}/{total_value}"


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _limited_text(
    value: Any,
    *,
    width: int,
    fallback: str,
) -> str:
    text = str(value).strip() if value is not None else ""
    return (text or fallback)[:width]


def _map_status(value: Any, mapping: dict[str, str]) -> str:
    if value is None:
        return "?"
    return mapping.get(str(value), "?")
