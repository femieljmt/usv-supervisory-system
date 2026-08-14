"""Tests for database-backed dashboard simulation and supporting sensors."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from dashboard.database_reader import DashboardDataReader
from server.database import TelemetryDatabase
from server.mission_database import MissionDatabase
from tests.test_database import make_payload


class DatabaseBackedSimulationTest(TestCase):
    def test_supporting_sensor_values_are_read_from_stored_payload_json(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(database_path)
            mission = MissionDatabase(database_path)

            payload = make_payload(1)
            payload.update(
                {
                    "data_source": "SIMULATION",
                    "supporting_data_source": "SIMULATION_DATABASE",
                    "water_temperature_c": 25.4,
                    "water_ph": 7.21,
                    "water_turbidity_ntu": 4.8,
                    "water_tds_ppm": 121.0,
                    "water_dissolved_oxygen_mg_l": 7.4,
                    "water_conductivity_us_cm": 241.0,
                    "solar_pv_voltage_v": 18.2,
                    "solar_pv_current_a": 3.1,
                    "solar_pv_power_w": 56.42,
                    "solar_battery_voltage_v": 25.8,
                    "solar_charge_current_a": 2.4,
                    "solar_state_of_charge_pct": 81.0,
                }
            )
            telemetry.store_telemetry(payload)

            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01"
            )
            supporting = snapshot["supporting_sensors"]

            self.assertEqual(supporting["status"], "DATA")
            self.assertEqual(supporting["source"], "SIMULATION_DATABASE")
            self.assertEqual(supporting["latest"]["water_ph"], 7.21)
            self.assertEqual(supporting["latest"]["solar_pv_power_w"], 56.42)
            self.assertEqual(len(supporting["records"]), 1)

    def test_session_without_sensor_fields_returns_no_data(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "server.sqlite3"
            telemetry = TelemetryDatabase(database_path)
            mission = MissionDatabase(database_path)
            telemetry.store_telemetry(make_payload(1))

            snapshot = DashboardDataReader(telemetry, mission).snapshot(
                vehicle_id="usv-01"
            )
            self.assertEqual(snapshot["supporting_sensors"]["status"], "NO_DATA")
            self.assertEqual(snapshot["supporting_sensors"]["records"], [])

    def test_simulator_contains_database_pipeline_and_sensor_fields(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/simulate_telemetry.py").read_text(encoding="utf-8")
        javascript = (root / "dashboard/static/js/dashboard.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("MQTT -> server.app -> SQLite -> dashboard", script)
        self.assertIn('"water_temperature_c"', script)
        self.assertIn('"solar_pv_power_w"', script)
        self.assertIn("supporting_sensors", javascript)
        self.assertIn("drawLineChart($(\"waterTempPhChart\")", javascript)
