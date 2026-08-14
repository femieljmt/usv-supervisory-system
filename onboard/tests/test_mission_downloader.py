"""Unit tests for MAVLink mission-plan downloading."""

from __future__ import annotations

from unittest import TestCase

from pymavlink import mavutil

from onboard.mission_downloader import MissionDownloader
from onboard.mission_plan import MissionPlanStore


class FakeMAV:
    """Record MAVLink mission requests sent by MissionDownloader."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, tuple]] = []
        self.acks: list[tuple] = []

    def mission_request_list_send(
        self,
        *arguments,
    ) -> None:
        self.requests.append(
            ("LIST", arguments)
        )

    def mission_request_int_send(
        self,
        *arguments,
    ) -> None:
        self.requests.append(
            ("ITEM", arguments)
        )

    def mission_ack_send(
        self,
        *arguments,
    ) -> None:
        self.acks.append(arguments)


class FakeConnection:
    """Minimal MAVLink connection used by the unit test."""

    def __init__(self) -> None:
        self.target_system = 1
        self.target_component = 1
        self.mav = FakeMAV()


class FakeMessage:
    """Minimal MAVLink-like message."""

    def __init__(
        self,
        message_type: str,
        **fields,
    ) -> None:
        self._message_type = message_type

        for field_name, value in fields.items():
            setattr(
                self,
                field_name,
                value,
            )

    def get_type(self) -> str:
        return self._message_type

    def get_srcSystem(self) -> int:
        return 1

    def get_srcComponent(self) -> int:
        return 1


def mission_type_value() -> int:
    return int(
        mavutil.mavlink.MAV_MISSION_TYPE_MISSION
    )


def waypoint_command_value() -> int:
    return int(
        mavutil.mavlink.MAV_CMD_NAV_WAYPOINT
    )


def global_frame_value() -> int:
    return int(
        getattr(
            mavutil.mavlink,
            "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT",
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
        )
    )


class MissionDownloaderTest(TestCase):

    def test_requests_mission_list_after_heartbeat(
        self,
    ) -> None:
        store = MissionPlanStore()
        connection = FakeConnection()

        downloader = MissionDownloader(
            store
        )

        downloader.on_heartbeat(
            FakeMessage(
                "HEARTBEAT",
                type=(
                    mavutil.mavlink
                    .MAV_TYPE_SURFACE_BOAT
                ),
            ),
            connection,
        )

        self.assertTrue(
            downloader.active
        )

        self.assertGreaterEqual(
            len(connection.mav.requests),
            1,
        )

        self.assertEqual(
            connection.mav.requests[0][0],
            "LIST",
        )

    def test_downloads_home_and_waypoints(
        self,
    ) -> None:
        store = MissionPlanStore()
        connection = FakeConnection()

        downloader = MissionDownloader(
            store
        )

        downloader.on_heartbeat(
            FakeMessage(
                "HEARTBEAT",
                type=(
                    mavutil.mavlink
                    .MAV_TYPE_SURFACE_BOAT
                ),
            ),
            connection,
        )

        downloader.handle_message(
            FakeMessage(
                "MISSION_COUNT",
                count=3,
                mission_type=(
                    mission_type_value()
                ),
                opaque_id=100,
            ),
            connection,
        )

        mission_items = [
            (
                0,
                2.1000000,
                99.1000000,
            ),
            (
                1,
                2.1100000,
                99.1100000,
            ),
            (
                2,
                2.1200000,
                99.1200000,
            ),
        ]

        for (
            sequence,
            latitude,
            longitude,
        ) in mission_items:
            downloader.handle_message(
                FakeMessage(
                    "MISSION_ITEM_INT",
                    seq=sequence,
                    command=(
                        waypoint_command_value()
                    ),
                    frame=(
                        global_frame_value()
                    ),
                    mission_type=(
                        mission_type_value()
                    ),
                    current=(
                        1
                        if sequence == 0
                        else 0
                    ),
                    autocontinue=1,
                    param1=0.0,
                    param2=0.0,
                    param3=0.0,
                    param4=0.0,
                    x=int(
                        latitude * 1e7
                    ),
                    y=int(
                        longitude * 1e7
                    ),
                    z=0.0,
                ),
                connection,
            )

        snapshot = (
            store.get_snapshot()
        )

        self.assertTrue(
            snapshot.complete
        )

        self.assertEqual(
            snapshot.total_count,
            3,
        )

        self.assertEqual(
            snapshot.executable_count,
            2,
        )

        self.assertTrue(
            snapshot.items[0].is_home
        )

        self.assertFalse(
            snapshot.items[1].is_home
        )

        self.assertAlmostEqual(
            snapshot.items[1].lat,
            2.11,
        )

        self.assertAlmostEqual(
            snapshot.items[2].lon,
            99.12,
        )

        self.assertEqual(
            snapshot.opaque_id,
            100,
        )

        self.assertFalse(
            downloader.active
        )

        self.assertEqual(
            len(connection.mav.acks),
            1,
        )

    def test_requests_items_in_sequence(
        self,
    ) -> None:
        store = MissionPlanStore()
        connection = FakeConnection()

        downloader = MissionDownloader(
            store
        )

        downloader.on_heartbeat(
            FakeMessage(
                "HEARTBEAT",
                type=(
                    mavutil.mavlink
                    .MAV_TYPE_SURFACE_BOAT
                ),
            ),
            connection,
        )

        downloader.handle_message(
            FakeMessage(
                "MISSION_COUNT",
                count=2,
                mission_type=(
                    mission_type_value()
                ),
            ),
            connection,
        )

        request_types = [
            request[0]
            for request
            in connection.mav.requests
        ]

        self.assertIn(
            "LIST",
            request_types,
        )

        self.assertIn(
            "ITEM",
            request_types,
        )
