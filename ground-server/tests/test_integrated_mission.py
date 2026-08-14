"""Tests for mission processing through the integrated MQTT backend layer."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from server.mission_database import MissionDatabase
from server.mqtt_backend import MissionMessageProcessor, extract_vehicle_id
from tests.test_mission_database import make_payload


class IntegratedMissionTest(TestCase):
    def test_mission_message_is_stored(self) -> None:
        with TemporaryDirectory() as directory:
            database = MissionDatabase(Path(directory) / "server.sqlite3")
            processor = MissionMessageProcessor(database)
            payload = make_payload()

            result = processor.process(
                "usv/usv-01/waypoints",
                json.dumps(payload).encode("utf-8"),
            )

            self.assertTrue(result.store_result.inserted)
            self.assertEqual(database.count_plans(), 1)
            self.assertEqual(database.get_latest("usv-01")["mission_total"], 3)

    def test_mission_topic_parser(self) -> None:
        self.assertEqual(
            extract_vehicle_id("usv/usv-01/waypoints", expected_suffix="waypoints"),
            "usv-01",
        )
        with self.assertRaises(ValueError):
            extract_vehicle_id("usv/usv-01/telemetry", expected_suffix="waypoints")
