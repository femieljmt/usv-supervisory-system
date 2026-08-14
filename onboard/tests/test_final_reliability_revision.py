"""Final reliability regressions for mission and graceful shutdown behavior."""

from __future__ import annotations

from unittest import TestCase

from pymavlink import mavutil

from onboard.app import drain_outbox_before_shutdown
from onboard.mavlink_reader import MAVLinkReader
from onboard.mission_downloader import MissionDownloader
from onboard.mission_plan import MissionItem, MissionPlanStore
from onboard.models import TelemetryStore
from tests.test_mavlink_reader import FakeMessage, make_settings
from tests.test_mission_downloader import FakeConnection, mission_type_value


class _DrainingOutbox:
    def __init__(self, count: int) -> None:
        self.pending = count

    def count(self) -> int:
        return self.pending


class _DrainingSynchronizer:
    def __init__(self, outbox: _DrainingOutbox) -> None:
        self.outbox = outbox
        self.in_flight_count = 0

    def notify_new_record(self) -> None:
        if self.outbox.pending:
            self.outbox.pending -= 1


class FinalOnboardReliabilityTest(TestCase):
    def test_graceful_shutdown_drain_stops_with_empty_outbox(self) -> None:
        outbox = _DrainingOutbox(3)
        synchronizer = _DrainingSynchronizer(outbox)
        completed, _, pending = drain_outbox_before_shutdown(
            outbox=outbox,
            synchronizer=synchronizer,
            timeout_seconds=1.0,
            poll_interval_seconds=0.01,
        )
        self.assertTrue(completed)
        self.assertEqual(pending, 0)
        self.assertEqual(outbox.count(), 0)

    def test_mission_payload_carries_revision(self) -> None:
        store = MissionPlanStore()
        snapshot = store.set_complete(
            [
                MissionItem(
                    seq=1,
                    command=16,
                    command_name="MAV_CMD_NAV_WAYPOINT",
                    frame=3,
                    frame_name="MAV_FRAME_GLOBAL_RELATIVE_ALT",
                    is_home=False,
                    current=False,
                    autocontinue=True,
                    param1=0.0,
                    param2=0.0,
                    param3=0.0,
                    param4=0.0,
                    lat=2.38,
                    lon=99.14,
                    alt=0.0,
                )
            ]
        )
        payload = snapshot.to_payload(vehicle_id="usv-01", session_id="session-x")
        self.assertEqual(payload["revision"], snapshot.revision)

    def test_mission_item_reached_does_not_overwrite_active_waypoint(self) -> None:
        telemetry = TelemetryStore()
        reader = MAVLinkReader(make_settings(), telemetry)
        reader.process_message(FakeMessage("MISSION_COUNT", count=5))
        reader.process_message(FakeMessage("MISSION_CURRENT", seq=3))
        reader.process_message(FakeMessage("MISSION_ITEM_REACHED", seq=2))
        self.assertEqual(reader.get_snapshot().wp_index, 3)

    def test_external_upload_request_triggers_verification_without_final_ack(self) -> None:
        store = MissionPlanStore()
        connection = FakeConnection()
        downloader = MissionDownloader(store, change_debounce_seconds=0.0)
        downloader.on_heartbeat(
            FakeMessage("HEARTBEAT", type=mavutil.mavlink.MAV_TYPE_SURFACE_BOAT),
            connection,
        )
        downloader.handle_message(
            FakeMessage("MISSION_COUNT", count=0, mission_type=mission_type_value()),
            connection,
        )
        connection.mav.requests.clear()
        downloader.handle_message(
            FakeMessage(
                "MISSION_REQUEST_INT",
                seq=0,
                mission_type=mission_type_value(),
            ),
            connection,
        )
        downloader.tick(connection)
        self.assertTrue(any(kind == "LIST" for kind, _ in connection.mav.requests))
