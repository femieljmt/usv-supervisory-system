"""Tests for mission-plan logging and publication."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from onboard.mission_plan import (
    MissionItem,
    MissionPlanLogger,
    MissionPlanStore,
)
from onboard.mission_plan_coordinator import (
    MissionPlanCoordinator,
)
from onboard.mqtt_manager import PublishResult


class FakeMQTTManager:
    is_connected = True

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def publish_waypoints(
        self,
        payload: dict,
    ) -> PublishResult:
        self.payloads.append(
            dict(payload)
        )

        return PublishResult(
            accepted=True,
            topic="usv/usv-01/waypoints",
            rc=0,
            message_id=1,
        )


class MissionPlanCoordinatorTest(TestCase):

    def test_logs_and_publishes_plan(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            store = MissionPlanStore()

            store.set_complete(
                [
                    MissionItem(
                        seq=0,
                        command=16,
                        command_name=(
                            "MAV_CMD_NAV_WAYPOINT"
                        ),
                        frame=6,
                        frame_name=(
                            "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT"
                        ),
                        is_home=True,
                        current=True,
                        autocontinue=True,
                        param1=0.0,
                        param2=0.0,
                        param3=0.0,
                        param4=0.0,
                        lat=2.1,
                        lon=99.1,
                        alt=0.0,
                    ),
                    MissionItem(
                        seq=1,
                        command=16,
                        command_name=(
                            "MAV_CMD_NAV_WAYPOINT"
                        ),
                        frame=6,
                        frame_name=(
                            "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT"
                        ),
                        is_home=False,
                        current=False,
                        autocontinue=True,
                        param1=0.0,
                        param2=0.0,
                        param3=0.0,
                        param4=0.0,
                        lat=2.2,
                        lon=99.2,
                        alt=0.0,
                    ),
                ]
            )

            mqtt_manager = FakeMQTTManager()

            coordinator = (
                MissionPlanCoordinator(
                    vehicle_id="usv-01",
                    session_id="session-test",
                    store=store,
                    logger=MissionPlanLogger(
                        Path(directory)
                    ),
                    mqtt_manager=mqtt_manager,
                )
            )

            processed = (
                coordinator.process_once()
            )

            self.assertTrue(processed)
            self.assertEqual(
                len(mqtt_manager.payloads),
                1,
            )
            self.assertEqual(
                mqtt_manager.payloads[0]
                ["event_type"],
                "MISSION_PLAN",
            )
            self.assertEqual(
                len(
                    mqtt_manager.payloads[0]
                    ["waypoints"]
                ),
                2,
            )

            files = list(
                Path(directory).glob(
                    "mission_plan_*.json"
                )
            )

            self.assertEqual(
                len(files),
                1,
            )
    def test_uses_fixed_session_mission_file(self) -> None:
        with TemporaryDirectory() as directory:
            store = MissionPlanStore()
            store.set_complete(
                [
                    MissionItem(
                        seq=0,
                        command=16,
                        command_name="MAV_CMD_NAV_WAYPOINT",
                        frame=6,
                        frame_name="MAV_FRAME_GLOBAL_RELATIVE_ALT_INT",
                        is_home=True,
                        current=True,
                        autocontinue=True,
                        param1=0.0,
                        param2=0.0,
                        param3=0.0,
                        param4=0.0,
                        lat=2.1,
                        lon=99.1,
                        alt=0.0,
                    )
                ]
            )

            mission_file = (
                Path(directory)
                / "session-test"
                / "mission_plan.json"
            )
            coordinator = MissionPlanCoordinator(
                vehicle_id="usv-01",
                session_id="session-test",
                store=store,
                logger=MissionPlanLogger(
                    file_path=mission_file,
                    history_path=None,
                ),
                mqtt_manager=FakeMQTTManager(),
            )

            self.assertTrue(coordinator.process_once())
            self.assertTrue(mission_file.exists())
            self.assertEqual(mission_file.name, "mission_plan.json")



class MissionContentIdentityTest(TestCase):
    def _item(self, *, current: bool = False, lat: float = 2.0) -> MissionItem:
        return MissionItem(
            seq=1, command=16, command_name="MAV_CMD_NAV_WAYPOINT",
            frame=3, frame_name="MAV_FRAME_GLOBAL_RELATIVE_ALT",
            is_home=False, current=current, autocontinue=True,
            param1=0.0, param2=0.0, param3=0.0, param4=0.0,
            lat=lat, lon=99.0, alt=0.0,
        )

    def test_current_flag_change_is_not_new_mission(self) -> None:
        store = MissionPlanStore()
        first = store.set_complete([self._item(current=False)], opaque_id=10)
        second = store.set_complete([self._item(current=True)], opaque_id=10)
        self.assertEqual(first.revision, second.revision)

    def test_opaque_id_change_without_content_change_is_not_new_mission(self) -> None:
        store = MissionPlanStore()
        first = store.set_complete([self._item()], opaque_id=10)
        second = store.set_complete([self._item()], opaque_id=11)
        self.assertEqual(first.revision, second.revision)

    def test_waypoint_coordinate_change_creates_new_revision(self) -> None:
        store = MissionPlanStore()
        first = store.set_complete([self._item(lat=2.0)], opaque_id=10)
        second = store.set_complete([self._item(lat=2.1)], opaque_id=11)
        self.assertEqual(second.revision, first.revision + 1)
