"""Regression tests for final session, timestamp, mission and playback fixes."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from dashboard.database_reader import DashboardDataReader
from dashboard.routes import DashboardRoutes
from server.database import TelemetryDatabase
from server.mission_database import MissionDatabase
from tests.test_database import make_payload
from tests.test_mission_database import make_payload as make_mission


ROOT = Path(__file__).resolve().parents[1]


class FinalDashboardDataRevisionTest(TestCase):
    def test_sessions_sort_by_telemetry_time_and_receive_dynamic_rank(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)

            older = make_payload(1, session_id="20260716T040000Z-old")
            older["timestamp"] = "2026-07-16T04:00:01.000Z"
            newer = make_payload(1, session_id="20260720T010000Z-new")
            newer["timestamp"] = "2026-07-20T01:00:01.000Z"
            telemetry.store_telemetry(older)
            telemetry.store_telemetry(newer)

            # Reproduce the uploaded Pi Lab clock defect: the newer telemetry
            # was received with an older server timestamp.
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE telemetry_records SET backend_first_received_at=?, "
                    "backend_last_received_at=? WHERE session_id=?",
                    (
                        "2026-07-14T08:00:00.000Z",
                        "2026-07-14T08:00:00.000Z",
                        "20260720T010000Z-new",
                    ),
                )
                connection.commit()

            sessions = DashboardDataReader(telemetry, mission).list_sessions("usv-01")
            self.assertEqual(sessions[0]["session_id"], "20260720T010000Z-new")
            self.assertEqual(sessions[0]["rank"], 1)
            self.assertTrue(sessions[0]["is_latest"])
            self.assertEqual(sessions[1]["rank"], 2)

    def test_selected_session_never_uses_another_session_mission(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            telemetry.store_telemetry(make_payload(1, session_id="session-new"))
            old_mission = make_mission()
            old_mission["session_id"] = "session-old"
            mission.store(old_mission)

            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01", session_id="session-new"
            )
            self.assertIsNone(snapshot["mission"])
            self.assertEqual(snapshot["mission_availability"], "MISSING_FOR_SESSION")
            self.assertEqual(snapshot["latest_vehicle_mission_session_id"], "session-old")

    def test_clock_skew_uses_telemetry_timestamp_as_display_reference(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            payload = make_payload(1, session_id="20260720T010000Z-clock")
            payload["timestamp"] = "2026-07-20T01:00:00.000Z"
            telemetry.store_telemetry(payload)
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE telemetry_records SET backend_last_received_at=?",
                    ("2026-07-14T08:00:00.000Z",),
                )
                connection.commit()

            reader = DashboardDataReader(
                telemetry,
                mission,
                clock=lambda: datetime(2026, 7, 20, 1, 0, 3, tzinfo=timezone.utc),
            )
            freshness = reader.snapshot(vehicle_id="usv-01")["freshness"]
            self.assertTrue(freshness["clock_skew_detected"])
            self.assertEqual(freshness["timestamp_source"], "RECORD_TIMESTAMP")
            self.assertEqual(freshness["telemetry_at"], "2026-07-20T01:00:00.000Z")
            self.assertEqual(freshness["status"], "LIVE")

    def test_recent_and_playback_tracks_are_session_scoped(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            for seq in range(1, 21):
                payload = make_payload(seq, session_id="session-track")
                payload.update(
                    {
                        "timestamp": f"2026-07-20T01:00:{seq:02d}.000Z",
                        "lat": 2.38 + seq / 100000,
                        "lon": 99.14 + seq / 100000,
                        "gps_fix": 4,
                        "gps_fix_label": "DGPS",
                        "gps_hdop": 0.5,
                        "wp_index": min(seq, 5),
                    }
                )
                telemetry.store_telemetry(payload)

            reader = DashboardDataReader(telemetry, mission)
            recent = reader.snapshot(
                vehicle_id="usv-01",
                session_id="session-track",
                track_mode="recent",
                track_limit=5,
            )
            self.assertEqual([x["seq_id"] for x in recent["track"]], [16, 17, 18, 19, 20])
            playback = reader.playback_track(
                vehicle_id="usv-01", session_id="session-track", limit=100
            )
            self.assertEqual(playback["returned_points"], 20)
            self.assertEqual(playback["points"][0]["seq_id"], 1)
            self.assertEqual(playback["points"][-1]["seq_id"], 20)


class FinalDashboardRouteAndStaticTest(TestCase):
    def test_playback_route_returns_points(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dashboard" / "templates").mkdir(parents=True)
            (root / "dashboard" / "static").mkdir(parents=True)
            (root / "dashboard" / "templates" / "index.html").write_text("ok")
            path = root / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            item = make_payload(1, session_id="session-route")
            item.update({"lat": 2.38, "lon": 99.14, "gps_fix": 4, "gps_hdop": 0.5})
            telemetry.store_telemetry(item)
            routes = DashboardRoutes(
                project_root=root,
                reader=DashboardDataReader(telemetry, mission),
                default_track_limit=500,
            )
            status, _, body = routes.dispatch(
                "/api/track/playback?vehicle_id=usv-01&session_id=session-route"
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(json.loads(body)["points"]), 1)

    def test_static_dashboard_contains_final_controls(self) -> None:
        html = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "dashboard/static/js/dashboard.js").read_text(encoding="utf-8")
        gps_map = (ROOT / "dashboard/static/js/gps_map.js").read_text(encoding="utf-8")
        for element_id in (
            "trackLimitSelect",
            "playbackControls",
            "playbackSlider",
            "playbackPlayButton",
            "sessionSelectionMeta",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("[${rank}]", javascript)
        self.assertIn("clientFreshness", javascript)
        self.assertIn("activeWaypointSeq", gps_map)
        self.assertIn("waypoint-row", javascript)
