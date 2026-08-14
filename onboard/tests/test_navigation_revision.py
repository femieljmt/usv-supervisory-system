"""Regression tests for live mission refresh and GUIDED target telemetry."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from pymavlink import mavutil

from onboard.mavlink_reader import MAVLinkReader
from onboard.mission_downloader import MissionDownloader
from onboard.mission_plan import MissionPlanStore
from onboard.models import TelemetryStore


class FakeMAV:
    def __init__(self) -> None:
        self.requests: list[tuple[str, tuple]] = []

    def mission_request_list_send(self, *arguments) -> None:
        self.requests.append(("LIST", arguments))

    def mission_request_int_send(self, *arguments) -> None:
        self.requests.append(("ITEM", arguments))

    def mission_ack_send(self, *arguments) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.target_system = 1
        self.target_component = 1
        self.mav = FakeMAV()


class FakeMessage:
    def __init__(self, message_type: str, **fields) -> None:
        self._message_type = message_type
        for name, value in fields.items():
            setattr(self, name, value)

    def get_type(self) -> str:
        return self._message_type

    def get_srcSystem(self) -> int:
        return 1

    def get_srcComponent(self) -> int:
        return 1


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        mavlink_connection="udp:127.0.0.1:14551",
        mavlink_heartbeat_timeout=5.0,
        mission_refresh_interval_seconds=5.0,
        guided_target_message_interval_seconds=1.0,
    )


class MissionRefreshTest(TestCase):
    def _complete_empty_initial_download(self, downloader, connection, opaque_id=1):
        downloader.on_heartbeat(
            FakeMessage(
                "HEARTBEAT",
                type=mavutil.mavlink.MAV_TYPE_SURFACE_BOAT,
            ),
            connection,
        )
        downloader.handle_message(
            FakeMessage(
                "MISSION_COUNT",
                count=0,
                mission_type=mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
                opaque_id=opaque_id,
            ),
            connection,
        )

    def test_no_periodic_refresh_when_disabled(self) -> None:
        store = MissionPlanStore()
        connection = FakeConnection()
        downloader = MissionDownloader(
            store,
            refresh_interval_seconds=0.0,
            change_debounce_seconds=0.0,
        )

        with patch("onboard.mission_downloader.time.monotonic", return_value=0.0):
            self._complete_empty_initial_download(downloader, connection)

        with patch("onboard.mission_downloader.time.monotonic", return_value=600.0):
            downloader.tick(connection)

        self.assertEqual(
            [request[0] for request in connection.mav.requests].count("LIST"),
            1,
        )

    def test_mission_id_change_triggers_refresh(self) -> None:
        store = MissionPlanStore()
        connection = FakeConnection()
        downloader = MissionDownloader(
            store,
            refresh_interval_seconds=0.0,
            change_debounce_seconds=0.0,
        )

        with patch("onboard.mission_downloader.time.monotonic", return_value=0.0):
            self._complete_empty_initial_download(downloader, connection, opaque_id=10)
            downloader.handle_message(FakeMessage("MISSION_CURRENT", seq=0, mission_id=10), connection)

        with patch("onboard.mission_downloader.time.monotonic", return_value=3.0):
            downloader.handle_message(FakeMessage("MISSION_CURRENT", seq=0, mission_id=11), connection)
            downloader.tick(connection)

        self.assertEqual(
            [request[0] for request in connection.mav.requests].count("LIST"),
            2,
        )

    def test_external_autopilot_mission_ack_triggers_refresh(self) -> None:
        store = MissionPlanStore()
        connection = FakeConnection()
        downloader = MissionDownloader(
            store,
            refresh_interval_seconds=0.0,
            change_debounce_seconds=0.0,
        )
        with patch("onboard.mission_downloader.time.monotonic", return_value=0.0):
            self._complete_empty_initial_download(downloader, connection)

        with patch("onboard.mission_downloader.time.monotonic", return_value=3.0):
            downloader.handle_message(
                FakeMessage(
                    "MISSION_ACK",
                    type=mavutil.mavlink.MAV_MISSION_ACCEPTED,
                    mission_type=mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
                ),
                connection,
            )
            downloader.tick(connection)

        self.assertEqual(
            [request[0] for request in connection.mav.requests].count("LIST"),
            2,
        )

    def test_loopback_ack_inside_guard_does_not_trigger_refresh(self) -> None:
        store = MissionPlanStore()
        connection = FakeConnection()
        downloader = MissionDownloader(
            store,
            refresh_interval_seconds=0.0,
            change_debounce_seconds=0.0,
        )
        with patch("onboard.mission_downloader.time.monotonic", return_value=0.0):
            self._complete_empty_initial_download(downloader, connection)
            downloader.handle_message(
                FakeMessage(
                    "MISSION_ACK",
                    type=mavutil.mavlink.MAV_MISSION_ACCEPTED,
                    mission_type=mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
                ),
                connection,
            )
            downloader.tick(connection)

        self.assertEqual(
            [request[0] for request in connection.mav.requests].count("LIST"),
            1,
        )


class GuidedTargetReaderTest(TestCase):
    def test_global_guided_target_is_recorded(self) -> None:
        store = TelemetryStore()
        reader = MAVLinkReader(settings(), store)

        reader.process_message(
            FakeMessage(
                "HEARTBEAT",
                type=mavutil.mavlink.MAV_TYPE_SURFACE_BOAT,
                custom_mode=15,
                base_mode=0,
            )
        )
        reader.process_message(
            FakeMessage(
                "POSITION_TARGET_GLOBAL_INT",
                type_mask=0,
                lat_int=23854500,
                lon_int=991473800,
                alt=3.5,
            )
        )

        snapshot = reader.get_snapshot()
        self.assertEqual(snapshot.flight_mode, "GUIDED")
        self.assertTrue(snapshot.guided_target_valid)
        self.assertAlmostEqual(snapshot.guided_target_lat, 2.38545)
        self.assertAlmostEqual(snapshot.guided_target_lon, 99.14738)
        self.assertAlmostEqual(snapshot.guided_target_alt, 3.5)
        self.assertIsNotNone(snapshot.guided_target_updated_at)

    def test_guided_target_is_cleared_when_mode_changes(self) -> None:
        store = TelemetryStore()
        reader = MAVLinkReader(settings(), store)
        reader.process_message(
            FakeMessage(
                "HEARTBEAT",
                type=mavutil.mavlink.MAV_TYPE_SURFACE_BOAT,
                custom_mode=15,
                base_mode=0,
            )
        )
        reader.process_message(
            FakeMessage(
                "POSITION_TARGET_GLOBAL_INT",
                type_mask=0,
                lat_int=23854500,
                lon_int=991473800,
                alt=0.0,
            )
        )
        reader.process_message(
            FakeMessage(
                "HEARTBEAT",
                type=mavutil.mavlink.MAV_TYPE_SURFACE_BOAT,
                custom_mode=4,
                base_mode=0,
            )
        )

        snapshot = reader.get_snapshot()
        self.assertEqual(snapshot.flight_mode, "HOLD")
        self.assertFalse(snapshot.guided_target_valid)
        self.assertIsNone(snapshot.guided_target_lat)
        self.assertIsNone(snapshot.guided_target_lon)
