#!/usr/bin/env python3
"""Publish a complete database-backed dashboard simulation through MQTT.

Flow:
    simulator -> MQTT -> server.app -> SQLite -> dashboard API -> browser

All records are labelled SIMULATION. They are intended only to validate the
server, database queries, charts, and dashboard rendering. They are not field
measurement evidence and must not be presented as Pixhawk/sensor test data.
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from paho.mqtt import client as mqtt  # noqa: E402
from server.config import load_settings  # noqa: E402
from server.protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    ack_topic,
    telemetry_topic,
    waypoints_topic,
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def build_mission(vehicle_id: str, session_id: str) -> dict:
    base_lat = 2.38590
    base_lon = 99.14678
    points = [
        (base_lat, base_lon),
        (base_lat + 0.00035, base_lon + 0.00020),
        (base_lat + 0.00055, base_lon + 0.00065),
        (base_lat + 0.00015, base_lon + 0.00090),
        (base_lat - 0.00015, base_lon + 0.00050),
    ]
    waypoints = []
    for seq, (lat, lon) in enumerate(points):
        waypoints.append(
            {
                "seq": seq,
                "command": 16,
                "command_name": "MAV_CMD_NAV_WAYPOINT",
                "frame": 6,
                "frame_name": "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT",
                "is_home": seq == 0,
                "current": seq == 0,
                "autocontinue": True,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "lat": lat,
                "lon": lon,
                "alt": 0.0,
            }
        )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "event_type": "MISSION_PLAN",
        "vehicle_id": vehicle_id,
        "session_id": session_id,
        "generated_at": utcnow_iso(),
        "mission_total": len(waypoints),
        "executable_total": len(waypoints) - 1,
        "opaque_id": None,
        "data_source": "SIMULATION",
        "waypoints": waypoints,
    }


def phase_for(seq: int, count: int) -> tuple[str, str, str, str, int, str, int]:
    """Return state and delivery properties for a four-phase demo session."""

    quarter = max(1, count // 4)
    normal_1_end = quarter
    lost_end = min(count, quarter * 2)
    recovery_end = min(count, quarter * 3)

    if seq <= normal_1_end:
        return "NORMAL", "AVAILABLE", "CONNECTED", "LIVE", 0, "IDLE", 0

    if seq <= lost_end:
        buffer_count = seq - normal_1_end
        return (
            "GCS_LOST",
            "UNAVAILABLE",
            "DISCONNECTED",
            "REPLAY",
            buffer_count,
            "IDLE",
            1,
        )

    if seq <= recovery_end:
        recovery_length = max(1, recovery_end - lost_end)
        position = seq - lost_end
        buffer_count = max(0, recovery_length - position)
        return (
            "RECOVERY",
            "AVAILABLE",
            "CONNECTED",
            "REPLAY",
            buffer_count,
            "SYNCING" if buffer_count else "SYNCED",
            1,
        )

    return "NORMAL", "AVAILABLE", "CONNECTED", "LIVE", 0, "IDLE", 0


def supporting_values(seq: int, count: int) -> dict[str, float | str]:
    progress = seq / max(count, 1)
    wave = math.sin(progress * math.pi * 4.0)
    slow_wave = math.sin(progress * math.pi * 1.4)

    water_temperature = 24.8 + 0.9 * slow_wave + 0.15 * wave
    water_ph = 7.18 + 0.10 * math.sin(progress * math.pi * 3.0)
    turbidity = 4.5 + 1.8 * (wave + 1.0) / 2.0
    tds = 118.0 + 9.0 * slow_wave
    dissolved_oxygen = 7.35 - 0.20 * slow_wave
    conductivity = 238.0 + 18.0 * slow_wave

    sun = max(0.18, math.sin(progress * math.pi))
    pv_voltage = 17.2 + 1.7 * sun
    pv_current = 0.7 + 3.1 * sun
    pv_power = pv_voltage * pv_current
    battery_voltage = 25.2 + 0.7 * progress + 0.08 * wave
    charge_current = 0.4 + 2.4 * sun
    state_of_charge = min(100.0, 72.0 + 19.0 * progress)

    return {
        "supporting_data_source": "SIMULATION_DATABASE",
        "water_temperature_c": round(water_temperature, 3),
        "water_ph": round(water_ph, 3),
        "water_turbidity_ntu": round(turbidity, 3),
        "water_tds_ppm": round(tds, 3),
        "water_dissolved_oxygen_mg_l": round(dissolved_oxygen, 3),
        "water_conductivity_us_cm": round(conductivity, 3),
        "solar_pv_voltage_v": round(pv_voltage, 3),
        "solar_pv_current_a": round(pv_current, 3),
        "solar_pv_power_w": round(pv_power, 3),
        "solar_battery_voltage_v": round(battery_voltage, 3),
        "solar_charge_current_a": round(charge_current, 3),
        "solar_state_of_charge_pct": round(state_of_charge, 3),
    }


def build_payload(vehicle_id: str, session_id: str, seq: int, count: int) -> dict:
    state, internet, mqtt_status, delivery, buffer_count, sync, retry_count = phase_for(
        seq, count
    )
    angle = (seq / max(count, 1)) * math.pi * 1.5
    lat = 2.38590 + 0.00045 * math.sin(angle)
    lon = 99.14678 + 0.00060 * (seq / max(count, 1))
    ack_latency = 58.0 + 16.0 * (1.0 + math.sin(seq * 0.43))

    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "vehicle_id": vehicle_id,
        "session_id": session_id,
        "seq_id": seq,
        "timestamp": utcnow_iso(),
        "data_source": "SIMULATION",
        "delivery_type": delivery,
        "supervisor_state": state,
        "mission_status": "RUNNING" if seq < count else "COMPLETE",
        "ap_link": "OK",
        "internet_status": internet,
        "mqtt_connection_status": mqtt_status,
        "mqtt_publish_status": "PUBLISHED",
        "ack_status": "ACKED",
        "ack_latency_ms": round(ack_latency, 2),
        "retry_count": retry_count,
        "buffer_count": buffer_count,
        "sync_status": sync,
        "armed": True,
        "flight_mode": "AUTO",
        "lat": round(lat, 7),
        "lon": round(lon, 7),
        "heading": round((seq * 13.0) % 360, 1),
        "groundspeed": round(1.1 + 0.25 * math.sin(angle), 2),
        "battery_v": round(26.4 - seq * 0.008, 2),
        "battery_remaining_pct": max(0, 96 - seq // 3),
        "gps_fix": 4,
        "gps_fix_label": "DGPS",
        "gps_hdop": round(0.70 + 0.08 * abs(math.sin(angle)), 2),
        "wp_index": min(4, 1 + seq // max(1, count // 4)),
        "wp_total": 5,
        "wp_dist": round(max(0.0, 120.0 - seq * 1.45), 1),
        "mission_loaded": True,
        "mission_complete": seq == count,
        "simulation_profile": "DATABASE_DASHBOARD_DEMO_V1",
    }
    payload.update(supporting_values(seq, count))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate all dashboard panels through MQTT and SQLite."
    )
    parser.add_argument("--count", type=int, default=80)
    parser.add_argument("--interval", type=float, default=0.15)
    parser.add_argument("--vehicle", default="usv-sim-db-01")
    args = parser.parse_args()

    if args.count < 8:
        raise SystemExit("--count minimal 8 agar seluruh phase dapat ditampilkan")

    settings = load_settings()
    session_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-simdb-"
        + uuid.uuid4().hex[:6]
    )
    acked: set[int] = set()
    connected = threading.Event()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"usv-db-simulator-{uuid.uuid4().hex[:8]}",
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    def on_connect(client, userdata, flags, reason_code, properties):
        if getattr(reason_code, "is_failure", False):
            print("MQTT connection failed:", reason_code)
            return
        client.subscribe(ack_topic(args.vehicle), qos=settings.mqtt_qos)
        connected.set()

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if payload.get("session_id") == session_id:
                acked.add(int(payload["seq_id"]))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(settings.mqtt_host, settings.mqtt_port, settings.mqtt_keepalive)
    client.loop_start()
    if not connected.wait(10):
        raise SystemExit("Simulator failed to connect to MQTT")

    mission = build_mission(args.vehicle, session_id)
    client.publish(
        waypoints_topic(args.vehicle),
        json.dumps(mission, separators=(",", ":")),
        qos=settings.mqtt_qos,
        retain=True,
    ).wait_for_publish(5)

    print(f"Vehicle            : {args.vehicle}")
    print(f"Simulation session : {session_id}")
    print("Pipeline           : MQTT -> server.app -> SQLite -> dashboard")
    print("Data source        : SIMULATION / SIMULATION_DATABASE")

    for seq in range(1, args.count + 1):
        payload = build_payload(args.vehicle, session_id, seq, args.count)
        client.publish(
            telemetry_topic(args.vehicle),
            json.dumps(payload, separators=(",", ":")),
            qos=settings.mqtt_qos,
            retain=False,
        ).wait_for_publish(5)
        print(
            f"published seq={seq:05d} "
            f"state={payload['supervisor_state']:<8} "
            f"buffer={payload['buffer_count']:02d} "
            f"water={payload['water_temperature_c']:.2f}C "
            f"solar={payload['solar_pv_power_w']:.1f}W"
        )
        time.sleep(max(0.01, args.interval))

    deadline = time.time() + 15
    while len(acked) < args.count and time.time() < deadline:
        time.sleep(0.1)

    client.disconnect()
    client.loop_stop()
    print(f"ACK received       : {len(acked)}/{args.count}")
    print(f"Dashboard vehicle  : {args.vehicle}")
    print(f"Dashboard session  : {session_id}")
    print("WARNING: simulation data is not field-test evidence.")


if __name__ == "__main__":
    main()
