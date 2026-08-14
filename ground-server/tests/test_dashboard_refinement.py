"""Tests for session-aware dashboard refinement and evidence export."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from dashboard.database_reader import DashboardDataReader
from dashboard.routes import DashboardRoutes
from server.database import TelemetryDatabase
from server.mission_database import MissionDatabase
from tests.test_database import make_payload
from tests.test_mission_database import make_payload as make_mission


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class DashboardRefinementTest(TestCase):
    def test_session_summary_detects_missing_sequence_and_duplicate_delivery(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)

            first = make_payload(1, session_id="session-a")
            first.update({"supervisor_state": "NORMAL", "buffer_count": 0})
            third = make_payload(3, session_id="session-a", delivery_type="REPLAY")
            third.update(
                {
                    "supervisor_state": "RECOVERY",
                    "buffer_count": 4,
                    "retry_count": 2,
                }
            )
            telemetry.store_telemetry(first)
            telemetry.store_telemetry(third)
            telemetry.store_telemetry(third)  # duplicate delivery receipt
            telemetry.mark_ack_sent("usv-01", "session-a", 1)
            telemetry.mark_ack_sent("usv-01", "session-a", 3)

            latest = telemetry.get_latest_telemetry(
                "usv-01",
                session_id="session-a",
            )
            now = parse_iso(latest["backend_last_received_at"]) + timedelta(seconds=6)
            reader = DashboardDataReader(
                telemetry,
                mission,
                live_seconds=5,
                stale_seconds=15,
                clock=lambda: now,
            )
            snapshot = reader.snapshot(vehicle_id="usv-01", session_id="session-a")

            self.assertEqual(snapshot["freshness"]["status"], "STALE")
            self.assertEqual(snapshot["summary"]["missing_records"], 1)
            self.assertEqual(snapshot["summary"]["missing_sequences"], [2])
            self.assertEqual(snapshot["summary"]["duplicate_deliveries"], 1)
            self.assertEqual(snapshot["summary"]["replay_records"], 1)
            self.assertEqual(snapshot["summary"]["max_buffer_count"], 4)

    def test_selected_session_does_not_mix_state_history(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            telemetry.store_telemetry(make_payload(1, session_id="session-a"))
            second = make_payload(1, session_id="session-b")
            second["supervisor_state"] = "GCS_LOST"
            telemetry.store_telemetry(second)

            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01",
                session_id="session-a",
            )
            self.assertEqual(snapshot["session_id"], "session-a")
            self.assertEqual(len(snapshot["state_history"]), 1)
            self.assertEqual(snapshot["state_history"][0]["to_state"], "NORMAL")

    def test_mission_is_matched_to_selected_session(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            telemetry.store_telemetry(make_payload(1, session_id="session-a"))
            payload = make_mission()
            payload["session_id"] = "session-a"
            mission.store(payload)

            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01",
                session_id="session-a",
            )
            self.assertTrue(snapshot["mission"]["session_match"])
            self.assertEqual(snapshot["mission"]["session_id"], "session-a")

    def test_duplicate_of_old_record_does_not_replace_latest_operational_state(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            first = make_payload(1, session_id="session-a")
            first["supervisor_state"] = "GCS_LOST"
            second = make_payload(2, session_id="session-a")
            second["timestamp"] = "2026-07-10T01:00:02.000Z"
            second["supervisor_state"] = "NORMAL"
            telemetry.store_telemetry(first)
            telemetry.store_telemetry(second)
            telemetry.store_telemetry(first)  # old duplicate arrives last

            latest = telemetry.get_latest_telemetry(
                "usv-01", session_id="session-a"
            )
            self.assertEqual(latest["seq_id"], 2)
            self.assertEqual(latest["supervisor_state"], "NORMAL")

    def test_export_route_returns_session_csv_only(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dashboard" / "templates").mkdir(parents=True)
            (root / "dashboard" / "static").mkdir(parents=True)
            (root / "dashboard" / "templates" / "index.html").write_text(
                "<html></html>", encoding="utf-8"
            )
            path = root / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            telemetry.store_telemetry(make_payload(1, session_id="session-a"))
            telemetry.store_telemetry(make_payload(1, session_id="session-b"))
            routes = DashboardRoutes(
                project_root=root,
                reader=DashboardDataReader(telemetry, mission),
                default_track_limit=100,
            )

            status, content_type, body = routes.dispatch(
                "/api/export/session.csv?vehicle_id=usv-01&session_id=session-a"
            )
            decoded = body.decode("utf-8-sig")
            self.assertEqual(status, 200)
            self.assertIn("text/csv", content_type)
            self.assertIn("session-a", decoded)
            self.assertNotIn("session-b", decoded)

    def test_sessions_route_lists_newest_sessions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dashboard" / "templates").mkdir(parents=True)
            (root / "dashboard" / "static").mkdir(parents=True)
            (root / "dashboard" / "templates" / "index.html").write_text(
                "<html></html>", encoding="utf-8"
            )
            path = root / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            telemetry.store_telemetry(make_payload(1, session_id="session-a"))
            routes = DashboardRoutes(
                project_root=root,
                reader=DashboardDataReader(telemetry, mission),
                default_track_limit=100,
            )
            status, _, body = routes.dispatch("/api/sessions?vehicle_id=usv-01")
            payload = json.loads(body.decode("utf-8"))
            self.assertEqual(status, 200)
            self.assertEqual(payload["sessions"][0]["session_id"], "session-a")
