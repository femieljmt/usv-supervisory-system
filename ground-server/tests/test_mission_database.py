"""Tests for server-side mission-plan storage."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from server.mission_database import (
    MissionDatabase,
)


def make_payload() -> dict:
    return {
        "protocol_version": "1.0.0",
        "event_type": "MISSION_PLAN",
        "vehicle_id": "usv-01",
        "session_id": (
            "20260710T083000Z-test"
        ),
        "generated_at": (
            "2026-07-10T08:30:00.000Z"
        ),
        "mission_total": 3,
        "executable_total": 2,
        "opaque_id": 100,
        "waypoints": [
            {
                "seq": 0,
                "command": 16,
                "command_name": (
                    "MAV_CMD_NAV_WAYPOINT"
                ),
                "frame": 6,
                "frame_name": (
                    "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT"
                ),
                "is_home": True,
                "current": True,
                "autocontinue": True,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "lat": 2.1000000,
                "lon": 99.1000000,
                "alt": 0.0,
            },
            {
                "seq": 1,
                "command": 16,
                "command_name": (
                    "MAV_CMD_NAV_WAYPOINT"
                ),
                "frame": 6,
                "frame_name": (
                    "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT"
                ),
                "is_home": False,
                "current": False,
                "autocontinue": True,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "lat": 2.1100000,
                "lon": 99.1100000,
                "alt": 0.0,
            },
            {
                "seq": 2,
                "command": 16,
                "command_name": (
                    "MAV_CMD_NAV_WAYPOINT"
                ),
                "frame": 6,
                "frame_name": (
                    "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT"
                ),
                "is_home": False,
                "current": False,
                "autocontinue": True,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "lat": 2.1200000,
                "lon": 99.1200000,
                "alt": 0.0,
            },
        ],
    }


class MissionDatabaseTest(TestCase):

    def test_store_and_read_latest(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            database = MissionDatabase(
                Path(directory)
                / "test.sqlite3"
            )

            database.initialize()

            first = database.store(
                make_payload()
            )

            self.assertTrue(
                first.inserted
            )
            self.assertFalse(
                first.duplicate
            )

            latest = database.get_latest(
                "usv-01"
            )

            self.assertIsNotNone(latest)
            self.assertEqual(
                latest["mission_total"],
                3,
            )
            self.assertEqual(
                len(latest["waypoints"]),
                3,
            )

            self.assertEqual(
                database.count_plans(),
                1,
            )
            self.assertEqual(
                database.count_waypoints(),
                3,
            )

    def test_duplicate_is_not_inserted_twice(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            database = MissionDatabase(
                Path(directory)
                / "test.sqlite3"
            )

            database.initialize()

            first = database.store(
                make_payload()
            )
            second = database.store(
                make_payload()
            )

            self.assertTrue(
                first.inserted
            )
            self.assertFalse(
                second.inserted
            )
            self.assertTrue(
                second.duplicate
            )

            self.assertEqual(
                database.count_plans(),
                1,
            )
            self.assertEqual(
                database.count_waypoints(),
                3,
            )

    def test_rejects_wrong_mission_total(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            database = MissionDatabase(
                Path(directory)
                / "test.sqlite3"
            )

            database.initialize()

            payload = make_payload()
            payload["mission_total"] = 4

            with self.assertRaises(
                ValueError
            ):
                database.store(payload)
