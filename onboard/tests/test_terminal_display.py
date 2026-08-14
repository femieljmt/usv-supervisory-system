"""Tests for the final onboard running-log presentation."""

from __future__ import annotations

from datetime import datetime
from unittest import TestCase

from onboard.mission_plan import MissionItem, MissionPlanStore
from onboard.terminal_display import (
    format_mission_plan,
    format_terminal_event,
    format_terminal_record,
    operation_table_header,
    terminal_banner,
)
from onboard.terminal_events import TerminalEvent
from tests.test_ack_manager import make_payload


class TerminalDisplayTest(TestCase):

    def test_banner_contains_final_identity_and_batch(self) -> None:
        banner = terminal_banner(
            "usv-01",
            "100.64.0.20",
            1883,
            session_id="session-final",
            batch_size=10,
            session_directory="/data/sessions/session-final",
            operation_file="/data/sessions/session-final/operation.csv",
            runtime_file="/data/sessions/session-final/runtime.log",
            mission_file="/data/sessions/session-final/mission_plan.json",
            session_info_file="/data/sessions/session-final/session_info.json",
            outbox_path="/data/database/onboard_outbox.sqlite3",
            pending_outbox=3,
        )
        self.assertIn("USV SUPERVISORY OPERATION MONITOR", banner)
        self.assertIn("Vehicle   : usv-01", banner)
        self.assertIn("Session   : session-final", banner)
        self.assertIn("Batch     : 10 records", banner)
        self.assertIn("SESSION FILES", banner)
        self.assertIn("Operation : /data/sessions/session-final/operation.csv", banner)
        self.assertIn("Runtime   : /data/sessions/session-final/runtime.log", banner)
        self.assertIn("Mission   : /data/sessions/session-final/mission_plan.json", banner)
        self.assertIn("Info      : /data/sessions/session-final/session_info.json", banner)
        self.assertIn("PERSISTENT STORAGE", banner)
        self.assertIn("Pending   : 3 records", banner)

    def test_operation_header_excludes_internal_delivery_columns(self) -> None:
        header = operation_table_header()
        self.assertIn("STATE", header)
        self.assertIn("AP NET MQTT", header)
        self.assertIn("LOG BUF SYNC", header)
        self.assertNotIn("FLY", header)
        self.assertNotIn("WAIT_ACK", header)

    def test_operation_record_format(self) -> None:
        record = make_payload(1)
        record.update(
            {
                "flight_mode": "HOLD",
                "armed": False,
                "gps_fix_label": "3D_FIX",
                "gps_hdop": 0.9,
                "lat": 2.3843179,
                "lon": 99.1478236,
                "heading": 285.5,
                "groundspeed": 0.04,
                "battery_v": 11.7,
                "wp_index": 1,
                "wp_total": 9,
                "wp_dist": 12,
            }
        )
        line = format_terminal_record(
            record,
            backlog_count=0,
            display_sync_status="IDLE",
        )
        self.assertIn("[NORMAL", line)
        self.assertIn("seq=00001", line)
        self.assertIn("DIS HOLD", line)
        self.assertIn("ap=OK", line)
        self.assertIn("net=UP", line)
        self.assertIn("mqtt=UP", line)
        self.assertIn("log=OK", line)
        self.assertIn("buf=0", line)
        self.assertIn("wp=1/9", line)
        self.assertIn("lat=2.384318", line)
        self.assertIn("gps=3D", line)
        self.assertNotIn("FLY", line)
        self.assertNotIn("WAIT_ACK", line)

    def test_event_format(self) -> None:
        event = TerminalEvent(
            level="WARN",
            category="NETWORK",
            message="Internet onboard terputus",
            occurred_at=datetime(2026, 7, 10, 22, 23, 35),
        )
        line = format_terminal_event(event)
        self.assertIn("22:23:35", line)
        self.assertIn("[WARN ", line)
        self.assertIn("[NETWORK", line)
        self.assertIn("Internet onboard terputus", line)

    def test_mission_plan_format(self) -> None:
        store = MissionPlanStore()
        plan = store.set_complete(
            [
                MissionItem(
                    seq=0,
                    command=16,
                    command_name="MAV_CMD_NAV_WAYPOINT",
                    frame=6,
                    frame_name="MAV_FRAME_GLOBAL_RELATIVE_ALT_INT",
                    is_home=True,
                    current=True,
                    autocontinue=True,
                    param1=0.0,
                    param2=0.0,
                    param3=0.0,
                    param4=0.0,
                    lat=2.3843179,
                    lon=99.1478236,
                    alt=0.0,
                ),
                MissionItem(
                    seq=1,
                    command=16,
                    command_name="MAV_CMD_NAV_WAYPOINT",
                    frame=6,
                    frame_name="MAV_FRAME_GLOBAL_RELATIVE_ALT_INT",
                    is_home=False,
                    current=False,
                    autocontinue=True,
                    param1=0.0,
                    param2=0.0,
                    param3=0.0,
                    param4=0.0,
                    lat=2.3844200,
                    lon=99.1479300,
                    alt=0.0,
                ),
            ]
        )
        formatted = format_mission_plan(plan)
        self.assertIn("MISSION PLAN RECEIVED", formatted)
        self.assertIn("HOME", formatted)
        self.assertIn("MISSION", formatted)
        self.assertIn("NAV_WAYPOINT", formatted)
        self.assertIn("2.3844200", formatted)
