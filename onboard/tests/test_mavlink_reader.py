"""Tests for onboard MAVLink telemetry processing."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest import TestCase

from pymavlink import mavutil

from onboard.mavlink_reader import MAVLinkReader
from onboard.models import TelemetryStore


class FakeMessage:
    def __init__(
        self,
        message_type: str,
        **fields,
    ) -> None:
        self._message_type = message_type

        for name, value in fields.items():
            setattr(self, name, value)

    def get_type(self) -> str:
        return self._message_type


def make_settings(
    heartbeat_timeout: float = 5.0,
):
    return SimpleNamespace(
        mavlink_connection=(
            "udp:127.0.0.1:14551"
        ),
        mavlink_heartbeat_timeout=(
            heartbeat_timeout
        ),
    )


class MAVLinkReaderTest(TestCase):

    def test_heartbeat_updates_link_mode_and_armed(
        self,
    ) -> None:
        store = TelemetryStore()
        reader = MAVLinkReader(
            make_settings(),
            store,
        )

        reader.process_message(
            FakeMessage(
                "HEARTBEAT",
                type=(
                    mavutil.mavlink.MAV_TYPE_SURFACE_BOAT
                ),
                custom_mode=10,
                base_mode=(
                    mavutil.mavlink
                    .MAV_MODE_FLAG_SAFETY_ARMED
                ),
            )
        )

        snapshot = reader.get_snapshot()

        self.assertTrue(reader.pixhawk_available)
        self.assertTrue(snapshot.armed)
        self.assertEqual(
            snapshot.flight_mode,
            "AUTO",
        )
        self.assertIsNotNone(
            snapshot.last_heartbeat_at
        )

    def test_position_gps_and_speed_are_updated(
        self,
    ) -> None:
        store = TelemetryStore()
        reader = MAVLinkReader(
            make_settings(),
            store,
        )

        reader.process_message(
            FakeMessage(
                "GLOBAL_POSITION_INT",
                lat=21234567,
                lon=991234567,
                hdg=9050,
            )
        )

        reader.process_message(
            FakeMessage(
                "GPS_RAW_INT",
                fix_type=3,
                eph=90,
            )
        )

        reader.process_message(
            FakeMessage(
                "VFR_HUD",
                groundspeed=1.25,
                heading=91,
            )
        )

        snapshot = reader.get_snapshot()

        self.assertAlmostEqual(
            snapshot.lat,
            2.1234567,
        )
        self.assertAlmostEqual(
            snapshot.lon,
            99.1234567,
        )
        self.assertEqual(
            snapshot.gps_fix_label,
            "3D_FIX",
        )
        self.assertEqual(
            snapshot.gps_hdop,
            0.9,
        )
        self.assertEqual(
            snapshot.groundspeed,
            1.25,
        )
        self.assertEqual(
            snapshot.heading,
            91.0,
        )

    def test_battery_fields_are_updated(
        self,
    ) -> None:
        store = TelemetryStore()
        reader = MAVLinkReader(
            make_settings(),
            store,
        )

        reader.process_message(
            FakeMessage(
                "BATTERY_STATUS",
                voltages=[
                    15800,
                    65535,
                ],
                battery_remaining=80,
            )
        )

        snapshot = reader.get_snapshot()

        self.assertEqual(
            snapshot.battery_v,
            15.8,
        )
        self.assertEqual(
            snapshot.battery_remaining_pct,
            80,
        )

    def test_mission_information_is_updated(
        self,
    ) -> None:
        store = TelemetryStore()
        reader = MAVLinkReader(
            make_settings(),
            store,
        )

        reader.process_message(
            FakeMessage(
                "MISSION_COUNT",
                count=5,
            )
        )

        reader.process_message(
            FakeMessage(
                "MISSION_CURRENT",
                seq=2,
            )
        )

        reader.process_message(
            FakeMessage(
                "NAV_CONTROLLER_OUTPUT",
                wp_dist=12.54,
            )
        )

        snapshot = reader.get_snapshot()

        self.assertTrue(snapshot.mission_loaded)
        self.assertEqual(snapshot.wp_total, 5)
        self.assertEqual(snapshot.wp_index, 2)
        self.assertEqual(snapshot.wp_dist, 12.5)

    def test_heartbeat_timeout_marks_link_unavailable(
        self,
    ) -> None:
        store = TelemetryStore()
        reader = MAVLinkReader(
            make_settings(
                heartbeat_timeout=0.01
            ),
            store,
        )

        reader.process_message(
            FakeMessage(
                "HEARTBEAT",
                type=(
                    mavutil.mavlink.MAV_TYPE_SURFACE_BOAT
                ),
                custom_mode=4,
                base_mode=0,
            )
        )

        self.assertTrue(reader.pixhawk_available)

        time.sleep(0.03)

        self.assertFalse(reader.pixhawk_available)
