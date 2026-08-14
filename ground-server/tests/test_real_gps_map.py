"""Regression tests for the real GPS slippy map dashboard panel."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from dashboard.database_reader import DashboardDataReader
from server.database import TelemetryDatabase
from server.mission_database import MissionDatabase
from tests.test_database import make_payload


ROOT = Path(__file__).resolve().parents[1]


class RealGpsMapStaticTest(TestCase):
    def test_real_map_replaces_coordinate_svg(self) -> None:
        html = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        css = (ROOT / "dashboard/static/css/dashboard.css").read_text(encoding="utf-8")
        dashboard_js = (ROOT / "dashboard/static/js/dashboard.js").read_text(encoding="utf-8")
        map_js = (ROOT / "dashboard/static/js/gps_map.js").read_text(encoding="utf-8")

        self.assertIn('id="gpsMap"', html)
        self.assertIn('src="/static/js/gps_map.js"', html)
        self.assertNotIn('id="routeMap"', html)
        self.assertNotIn("function renderMap(", dashboard_js)
        self.assertIn("https://tile.openstreetmap.org/{z}/{x}/{y}.png", map_js)
        self.assertIn("function project(lat, lon, zoom)", map_js)
        self.assertIn("ResizeObserver", map_js)
        self.assertIn('addEventListener("error"', map_js)
        self.assertIn("Base map tidak tersedia", map_js)
        self.assertIn("height: 590px", css)
        self.assertIn(".map-tile-pane", css)
        self.assertIn(".map-marker.vehicle", css)

    def test_map_has_fixed_full_panel_and_controls(self) -> None:
        html = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        for element_id in (
            "mapFitButton",
            "mapCenterButton",
            "mapFollowButton",
            "mapMissionToggle",
            "mapTrackToggle",
            "mapZoomIn",
            "mapZoomOut",
            "gpsQualityBadge",
        ):
            self.assertIn(f'id="{element_id}"', html)


class RealGpsMapDataTest(TestCase):
    def test_dashboard_filters_invalid_gps_and_reports_quality(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(database_path)
            mission = MissionDatabase(database_path)

            good = make_payload(1)
            good.update(
                {
                    "lat": 2.38545,
                    "lon": 99.14738,
                    "gps_fix": 3,
                    "gps_fix_label": "3D",
                    "gps_hdop": 0.9,
                }
            )
            invalid = make_payload(2)
            invalid.update(
                {
                    "lat": 0.0,
                    "lon": 0.0,
                    "gps_fix": 1,
                    "gps_fix_label": "NO_FIX",
                    "gps_hdop": 99.0,
                }
            )
            degraded = make_payload(3)
            degraded.update(
                {
                    "lat": 2.38555,
                    "lon": 99.14748,
                    "gps_fix": 2,
                    "gps_fix_label": "2D",
                    "gps_hdop": 3.2,
                }
            )
            telemetry.store_telemetry(good)
            telemetry.store_telemetry(invalid)
            telemetry.store_telemetry(degraded)

            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01",
                session_id="session-test",
            )

            self.assertEqual([item["seq_id"] for item in snapshot["track"]], [1, 3])
            self.assertEqual(snapshot["gps_quality"]["status"], "DEGRADED")
            self.assertEqual(snapshot["gps_quality"]["valid_track_points"], 2)
            self.assertEqual(snapshot["gps_quality"]["rejected_track_points"], 1)
            self.assertEqual(snapshot["track"][-1]["gps_fix_label"], "2D")

    def test_no_fix_record_is_not_drawn(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(database_path)
            mission = MissionDatabase(database_path)
            payload = make_payload(1)
            payload.update(
                {
                    "lat": 2.38,
                    "lon": 99.14,
                    "gps_fix": 1,
                    "gps_fix_label": "NO_FIX",
                }
            )
            telemetry.store_telemetry(payload)
            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01",
                session_id="session-test",
            )
            self.assertEqual(snapshot["track"], [])
            self.assertEqual(snapshot["gps_quality"]["status"], "NO_FIX")
