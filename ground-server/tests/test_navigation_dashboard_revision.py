"""Regression tests for mission replacement, GUIDED targets and MANUAL segments."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from dashboard.database_reader import DashboardDataReader
from server.database import TelemetryDatabase
from server.mission_database import MissionDatabase
from tests.test_database import make_payload
from tests.test_mission_database import make_payload as make_mission


ROOT = Path(__file__).resolve().parents[1]


def gps_payload(seq_id: int, *, mode: str, lat: float, lon: float) -> dict:
    payload = make_payload(seq_id, session_id="session-nav")
    payload.update(
        {
            "timestamp": f"2026-07-11T01:00:{seq_id:02d}.000Z",
            "flight_mode": mode,
            "lat": lat,
            "lon": lon,
            "gps_fix": 4,
            "gps_fix_label": "DGPS",
            "gps_hdop": 0.8,
            "heading": 90.0,
            "groundspeed": 0.5,
        }
    )
    return payload


class NavigationDashboardDataTest(TestCase):
    def test_new_mission_same_count_replaces_old_coordinates(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            telemetry.store_telemetry(gps_payload(1, mode="HOLD", lat=2.38, lon=99.14))

            first = make_mission()
            first["session_id"] = "session-nav"
            first["opaque_id"] = 101
            mission.store(first)

            second = make_mission()
            second["session_id"] = "session-nav"
            second["opaque_id"] = 102
            second["generated_at"] = "2026-07-11T01:01:00.000Z"
            second["waypoints"][1]["lat"] = 2.385
            second["waypoints"][1]["lon"] = 99.145
            second["waypoints"][2]["lat"] = 2.386
            second["waypoints"][2]["lon"] = 99.146
            mission.store(second)

            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01", session_id="session-nav"
            )

            self.assertEqual(snapshot["mission"]["opaque_id"], 102)
            self.assertAlmostEqual(snapshot["mission"]["waypoints"][1]["lat"], 2.385)
            self.assertNotEqual(
                snapshot["mission"]["revision_key"],
                "",
            )
            self.assertEqual(mission.count_plans(), 2)

    def test_guided_target_is_exposed_only_in_guided_mode(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)
            guided = gps_payload(1, mode="GUIDED", lat=2.385, lon=99.145)
            guided.update(
                {
                    "guided_target_valid": True,
                    "guided_target_lat": 2.386,
                    "guided_target_lon": 99.146,
                    "guided_target_alt": 0.0,
                    "guided_target_updated_at": "2026-07-11T01:00:01.000Z",
                }
            )
            telemetry.store_telemetry(guided)
            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01", session_id="session-nav"
            )
            self.assertEqual(snapshot["guided_target"]["lat"], 2.386)
            self.assertEqual(snapshot["guided_target"]["lon"], 99.146)

            hold = gps_payload(2, mode="HOLD", lat=2.3851, lon=99.1451)
            hold.update(guided)
            hold.update(
                {
                    "seq_id": 2,
                    "timestamp": "2026-07-11T01:00:02.000Z",
                    "flight_mode": "HOLD",
                }
            )
            telemetry.store_telemetry(hold)
            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01", session_id="session-nav"
            )
            self.assertIsNone(snapshot["guided_target"])

    def test_only_latest_manual_segment_is_returned(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(path)
            mission = MissionDatabase(path)

            telemetry.store_telemetry(gps_payload(1, mode="MANUAL", lat=2.3800, lon=99.1400))
            telemetry.store_telemetry(gps_payload(2, mode="MANUAL", lat=2.3801, lon=99.1401))
            telemetry.store_telemetry(gps_payload(3, mode="HOLD", lat=2.3802, lon=99.1402))
            telemetry.store_telemetry(gps_payload(4, mode="MANUAL", lat=2.3810, lon=99.1410))
            telemetry.store_telemetry(gps_payload(5, mode="MANUAL", lat=2.3811, lon=99.1411))

            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01", session_id="session-nav"
            )
            segment = snapshot["manual_segment"]
            self.assertTrue(segment["active"])
            self.assertEqual([point["seq_id"] for point in segment["points"]], [4, 5])
            self.assertEqual(segment["start"]["seq_id"], 4)
            self.assertEqual(segment["end"]["seq_id"], 5)


class NavigationDashboardStaticTest(TestCase):
    def test_map_has_deep_zoom_and_navigation_layers(self) -> None:
        html = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        map_js = (ROOT / "dashboard/static/js/gps_map.js").read_text(encoding="utf-8")

        for element_id in (
            "mapFitButton",
            "mapFitTrackButton",
            "mapManualToggle",
            "mapGuidedToggle",
        ):
            self.assertIn(f'id="{element_id}"', html)

        self.assertIn("const MAX_ZOOM = 22", map_js)
        self.assertIn("const MAX_NATIVE_ZOOM = 19", map_js)
        self.assertIn("guidedTarget", map_js)
        self.assertIn("manualSegment", map_js)
        self.assertIn("fitMission(", map_js)
        self.assertIn("fitTrack(", map_js)
