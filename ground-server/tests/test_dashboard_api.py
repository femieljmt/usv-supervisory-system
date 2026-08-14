"""Tests for dashboard aggregation and HTTP routes."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from dashboard.database_reader import DashboardDataReader
from dashboard.routes import DashboardRoutes
from server.database import TelemetryDatabase
from server.mission_database import MissionDatabase
from tests.test_database import make_payload
from tests.test_mission_database import make_payload as make_mission


class DashboardApiTest(TestCase):
    def test_snapshot_contains_latest_track_and_mission(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(database_path)
            mission = MissionDatabase(database_path)

            first = make_payload(1)
            first.update({"lat": 2.1, "lon": 99.1, "groundspeed": 1.0})
            second = make_payload(2)
            second.update(
                {
                    "lat": 2.2,
                    "lon": 99.2,
                    "groundspeed": 1.2,
                    "supervisor_state": "GCS_LOST",
                }
            )
            telemetry.store_telemetry(first)
            telemetry.store_telemetry(second)
            mission_payload = make_mission()
            mission_payload["session_id"] = "session-test"
            mission.store(mission_payload)

            reader = DashboardDataReader(telemetry, mission)
            snapshot = reader.snapshot(vehicle_id="usv-01", track_limit=20)

            self.assertEqual(snapshot["latest"]["seq_id"], 2)
            self.assertEqual(len(snapshot["track"]), 2)
            self.assertEqual(snapshot["mission"]["mission_total"], 3)
            self.assertEqual(len(snapshot["state_history"]), 2)

    def test_routes_return_html_and_json(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dashboard" / "templates").mkdir(parents=True)
            (root / "dashboard" / "static" / "css").mkdir(parents=True)
            (root / "dashboard" / "templates" / "index.html").write_text(
                "<html>dashboard</html>",
                encoding="utf-8",
            )

            database_path = root / "server.sqlite3"
            telemetry = TelemetryDatabase(database_path)
            mission = MissionDatabase(database_path)
            telemetry.store_telemetry(make_payload(1))

            routes = DashboardRoutes(
                project_root=root,
                reader=DashboardDataReader(telemetry, mission),
                default_track_limit=100,
            )

            status, content_type, body = routes.dispatch("/")
            self.assertEqual(status, 200)
            self.assertIn("text/html", content_type)
            self.assertIn(b"dashboard", body)

            status, content_type, body = routes.dispatch(
                "/api/dashboard?vehicle_id=usv-01"
            )
            self.assertEqual(status, 200)
            payload = json.loads(body.decode("utf-8"))
            self.assertEqual(payload["vehicle_id"], "usv-01")
            self.assertEqual(payload["latest"]["seq_id"], 1)

class DashboardHttpServerTest(TestCase):
    def test_http_server_serves_health_endpoint(self) -> None:
        from urllib.request import urlopen

        from dashboard.app import DashboardServer

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dashboard" / "templates").mkdir(parents=True)
            (root / "dashboard" / "static").mkdir(parents=True)
            (root / "dashboard" / "templates" / "index.html").write_text(
                "<html>dashboard</html>",
                encoding="utf-8",
            )
            telemetry = TelemetryDatabase(root / "server.sqlite3")
            mission = MissionDatabase(root / "server.sqlite3")
            routes = DashboardRoutes(
                project_root=root,
                reader=DashboardDataReader(telemetry, mission),
                default_track_limit=100,
            )
            server = DashboardServer(host="127.0.0.1", port=0, routes=routes)
            server.start()
            try:
                host, port = server.address
                with urlopen(f"http://{host}:{port}/api/health", timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["status"], "ok")
            finally:
                server.stop()
