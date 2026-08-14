"""MAVLink telemetry and mission reader for Raspberry Pi USV.

Modul ini membaca data Pixhawk melalui output UDP MAVProxy.

Alur komunikasi:

Pixhawk
→ MAVProxy
→ UDP 127.0.0.1:14551
→ MAVLinkReader
→ TelemetryStore
→ MissionDownloader
→ MissionPlanStore

Modul ini tidak mengambil alih navigasi, tidak mengubah waypoint,
dan tidak menentukan supervisor state.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from pymavlink import mavutil

from onboard.config import Settings
from onboard.mission_downloader import (
    MissionDownloader,
)
from onboard.mission_plan import (
    MissionPlanStore,
)
from onboard.models import (
    TelemetrySnapshot,
    TelemetryStore,
)


LOGGER = logging.getLogger(__name__)


GPS_FIX_LABELS = {
    0: "NO_GPS",
    1: "NO_FIX",
    2: "2D_FIX",
    3: "3D_FIX",
    4: "DGPS",
    5: "RTK_FLOAT",
    6: "RTK_FIXED",
}


ARDUROVER_MODES = {
    0: "MANUAL",
    1: "ACRO",
    3: "STEERING",
    4: "HOLD",
    10: "AUTO",
    11: "RTL",
    12: "SMART_RTL",
    15: "GUIDED",
    16: "INITIALISING",
}


class MAVLinkReader:
    """Read MAVLink telemetry and mission data in a background thread."""

    def __init__(
        self,
        settings: Settings,
        telemetry_store: TelemetryStore,
        mission_store: MissionPlanStore | None = None,
    ) -> None:
        self._settings = settings
        self._telemetry_store = telemetry_store

        self._mission_store = (
            mission_store
            if mission_store is not None
            else MissionPlanStore()
        )

        self._mission_downloader = MissionDownloader(
            self._mission_store,
            refresh_interval_seconds=(
                getattr(
                    self._settings,
                    "mission_refresh_interval_seconds",
                    5.0,
                )
            ),
        )

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection: Any | None = None

        self._lock = threading.RLock()

        self._last_message_monotonic: float | None = None
        self._last_heartbeat_monotonic: float | None = None

        self._stream_requested = False

    @property
    def running(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
        )

    @property
    def pixhawk_available(self) -> bool:
        """Return True when Pixhawk heartbeat is still fresh."""

        age = self.heartbeat_age_seconds

        if age is None:
            return False

        return (
            age
            <= self._settings.mavlink_heartbeat_timeout
        )

    @property
    def heartbeat_age_seconds(
        self,
    ) -> float | None:
        with self._lock:
            last_heartbeat = (
                self._last_heartbeat_monotonic
            )

        if last_heartbeat is None:
            return None

        return max(
            0.0,
            time.monotonic() - last_heartbeat,
        )

    @property
    def message_age_seconds(
        self,
    ) -> float | None:
        with self._lock:
            last_message = (
                self._last_message_monotonic
            )

        if last_message is None:
            return None

        return max(
            0.0,
            time.monotonic() - last_message,
        )

    @property
    def mission_store(self) -> MissionPlanStore:
        """Return the mission-plan storage used by this reader."""

        return self._mission_store

    def get_snapshot(self) -> TelemetrySnapshot:
        return self._telemetry_store.get_snapshot()

    def start(self) -> None:
        """Start the background MAVLink receiver."""

        if self.running:
            return

        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run,
            name="mavlink-reader",
            daemon=True,
        )

        self._thread.start()

    def stop(self) -> None:
        """Stop the background reader and close MAVLink connection."""

        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=5.0)

        connection = self._connection
        self._connection = None

        if connection is not None:
            try:
                connection.close()
            except Exception:
                LOGGER.exception(
                    "Kesalahan saat menutup koneksi MAVLink"
                )

    def process_message(
        self,
        message: Any,
    ) -> None:
        """Process one telemetry-related MAVLink message.

        Method ini juga digunakan oleh unit test tanpa Pixhawk fisik.
        Mission protocol messages diproses secara terpisah oleh
        MissionDownloader pada loop pembacaan.
        """

        message_type = message.get_type()

        if message_type == "BAD_DATA":
            return

        received_at = utcnow_iso()
        received_monotonic = time.monotonic()

        with self._lock:
            self._last_message_monotonic = (
                received_monotonic
            )

        self._telemetry_store.update(
            last_message_at=received_at
        )

        if message_type == "HEARTBEAT":
            self._process_heartbeat(
                message,
                received_at,
                received_monotonic,
            )

        elif message_type == "GLOBAL_POSITION_INT":
            self._process_global_position(
                message
            )

        elif message_type == "VFR_HUD":
            self._process_vfr_hud(
                message
            )

        elif message_type == "BATTERY_STATUS":
            self._process_battery_status(
                message
            )

        elif message_type == "SYS_STATUS":
            self._process_sys_status(
                message
            )

        elif message_type == "GPS_RAW_INT":
            self._process_gps_raw(
                message
            )

        elif message_type == "MISSION_CURRENT":
            self._process_mission_current(
                message
            )

        elif message_type == "MISSION_COUNT":
            self._process_mission_count(
                message
            )

        elif message_type == "NAV_CONTROLLER_OUTPUT":
            self._process_navigation_output(
                message
            )

        elif message_type == "MISSION_ITEM_REACHED":
            self._process_mission_item_reached(
                message
            )

        elif message_type in {
            "POSITION_TARGET_GLOBAL_INT",
            "SET_POSITION_TARGET_GLOBAL_INT",
        }:
            self._process_guided_target(
                message,
                received_at,
            )

    def _run(self) -> None:
        LOGGER.info(
            "MAVLink reader dimulai: %s",
            self._settings.mavlink_connection,
        )

        while not self._stop_event.is_set():
            try:
                if self._connection is None:
                    self._open_connection()

                message = self._connection.recv_match(
                    blocking=True,
                    timeout=1.0,
                )

                if message is None:
                    self._mission_downloader.tick(
                        self._connection,
                        allow_refresh=self.pixhawk_available,
                    )
                    continue

                # Proses telemetry umum.
                self.process_message(
                    message
                )

                # Proses protocol pengunduhan mission plan.
                self._mission_downloader.handle_message(
                    message,
                    self._connection,
                )

                # Periksa timeout dan retry mission request.
                self._mission_downloader.tick(
                    self._connection,
                    allow_refresh=self.pixhawk_available,
                )

            except Exception:
                LOGGER.exception(
                    "Kesalahan pembacaan MAVLink"
                )

                self._close_connection()

                if self._stop_event.wait(1.0):
                    break

        LOGGER.info(
            "MAVLink reader dihentikan"
        )

    def _open_connection(self) -> None:
        self._connection = (
            mavutil.mavlink_connection(
                self._settings.mavlink_connection,
                autoreconnect=True,
            )
        )

        self._stream_requested = False

        # Downloader baru dibuat setiap koneksi MAVLink dibuka kembali.
        # Dengan demikian mission plan akan diminta ulang setelah
        # Pixhawk atau jalur MAVProxy tersambung kembali.
        self._mission_downloader = MissionDownloader(
            self._mission_store,
            refresh_interval_seconds=(
                getattr(
                    self._settings,
                    "mission_refresh_interval_seconds",
                    5.0,
                )
            ),
        )

        LOGGER.info(
            "Koneksi MAVLink dibuka: %s",
            self._settings.mavlink_connection,
        )

    def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None

        if connection is None:
            return

        try:
            connection.close()
        except Exception:
            LOGGER.exception(
                "Kesalahan saat menutup ulang koneksi MAVLink"
            )

    def _process_heartbeat(
        self,
        message: Any,
        received_at: str,
        received_monotonic: float,
    ) -> None:
        """Process autopilot heartbeat and start mission download."""

        # Heartbeat dari GCS tidak dianggap sebagai heartbeat Pixhawk.
        if (
            getattr(message, "type", None)
            == mavutil.mavlink.MAV_TYPE_GCS
        ):
            return

        custom_mode = int(
            getattr(
                message,
                "custom_mode",
                0,
            )
        )

        flight_mode = ARDUROVER_MODES.get(
            custom_mode,
            f"MODE_{custom_mode}",
        )

        base_mode = int(
            getattr(
                message,
                "base_mode",
                0,
            )
        )

        armed = bool(
            base_mode
            & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        )

        with self._lock:
            self._last_heartbeat_monotonic = (
                received_monotonic
            )

        heartbeat_changes = {
            "armed": armed,
            "flight_mode": flight_mode,
            "last_heartbeat_at": received_at,
        }

        # A GUIDED target is dynamic context, not a permanent mission item.
        # Clear it as soon as the vehicle leaves GUIDED mode so the dashboard
        # never keeps displaying an obsolete guided destination.
        if flight_mode != "GUIDED":
            heartbeat_changes.update(
                {
                    "guided_target_lat": None,
                    "guided_target_lon": None,
                    "guided_target_alt": None,
                    "guided_target_valid": False,
                    "guided_target_updated_at": None,
                }
            )

        self._telemetry_store.update(**heartbeat_changes)

        self._request_data_stream_once()

        connection = self._connection

        if connection is not None:
            self._mission_downloader.on_heartbeat(
                message,
                connection,
            )

    def _request_data_stream_once(self) -> None:
        """Request MAVLink telemetry stream once per connection."""

        if self._stream_requested:
            return

        connection = self._connection

        if connection is None:
            return

        target_system = int(
            getattr(
                connection,
                "target_system",
                0,
            )
        )

        target_component = int(
            getattr(
                connection,
                "target_component",
                0,
            )
        )

        if target_system <= 0:
            return

        try:
            connection.mav.request_data_stream_send(
                target_system,
                target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                4,
                1,
            )

            self._stream_requested = True

            LOGGER.info(
                "Permintaan MAVLink data stream dikirim"
            )

            # Ask the autopilot to report the active global target used by
            # GUIDED mode. This is harmless if the firmware does not support
            # the requested message interval.
            interval_us = int(
                getattr(
                    self._settings,
                    "guided_target_message_interval_seconds",
                    1.0,
                )
                * 1_000_000
            )
            try:
                connection.mav.command_long_send(
                    target_system,
                    target_component,
                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                    0,
                    int(
                        mavutil.mavlink.MAVLINK_MSG_ID_POSITION_TARGET_GLOBAL_INT
                    ),
                    interval_us,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
                LOGGER.info(
                    "Stream GUIDED target diminta: interval=%.1fs",
                    getattr(
                        self._settings,
                        "guided_target_message_interval_seconds",
                        1.0,
                    ),
                )
            except Exception:
                LOGGER.warning(
                    "Pixhawk tidak menerima permintaan stream GUIDED target",
                    exc_info=True,
                )

        except Exception:
            LOGGER.exception(
                "Gagal meminta MAVLink data stream"
            )

    def _process_guided_target(
        self,
        message: Any,
        received_at: str,
    ) -> None:
        """Store the latest global target used by GUIDED mode."""

        lat_raw = getattr(message, "lat_int", None)
        lon_raw = getattr(message, "lon_int", None)
        type_mask = int(getattr(message, "type_mask", 0) or 0)

        # POSITION_TARGET_TYPEMASK bits 0 and 1 indicate that X/Y position
        # should be ignored. A target without latitude/longitude is not
        # useful for map display.
        if (type_mask & 0b11) == 0b11:
            return

        try:
            latitude = float(lat_raw) / 1e7
            longitude = float(lon_raw) / 1e7
        except (TypeError, ValueError):
            return

        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            return
        if abs(latitude) < 1e-12 and abs(longitude) < 1e-12:
            return

        altitude_raw = getattr(message, "alt", None)
        try:
            altitude = float(altitude_raw) if altitude_raw is not None else None
        except (TypeError, ValueError):
            altitude = None

        self._telemetry_store.update(
            guided_target_lat=latitude,
            guided_target_lon=longitude,
            guided_target_alt=altitude,
            guided_target_valid=True,
            guided_target_updated_at=received_at,
        )

        LOGGER.info(
            "GUIDED target diperbarui: lat=%.7f lon=%.7f alt=%s",
            latitude,
            longitude,
            "--" if altitude is None else f"{altitude:.1f}",
        )

    def _process_global_position(
        self,
        message: Any,
    ) -> None:
        lat_raw = getattr(
            message,
            "lat",
            None,
        )

        lon_raw = getattr(
            message,
            "lon",
            None,
        )

        heading_raw = getattr(
            message,
            "hdg",
            None,
        )

        updates: dict[str, Any] = {}

        if lat_raw is not None:
            updates["lat"] = round(
                float(lat_raw) / 1e7,
                7,
            )

        if lon_raw is not None:
            updates["lon"] = round(
                float(lon_raw) / 1e7,
                7,
            )

        if (
            heading_raw is not None
            and int(heading_raw) != 65535
        ):
            updates["heading"] = round(
                float(heading_raw) / 100.0,
                2,
            )

        if updates:
            self._telemetry_store.update(
                **updates
            )

    def _process_vfr_hud(
        self,
        message: Any,
    ) -> None:
        updates: dict[str, Any] = {}

        groundspeed = getattr(
            message,
            "groundspeed",
            None,
        )

        heading = getattr(
            message,
            "heading",
            None,
        )

        if groundspeed is not None:
            updates["groundspeed"] = round(
                float(groundspeed),
                2,
            )

        if heading is not None:
            updates["heading"] = round(
                float(heading),
                2,
            )

        if updates:
            self._telemetry_store.update(
                **updates
            )

    def _process_battery_status(
        self,
        message: Any,
    ) -> None:
        updates: dict[str, Any] = {}

        voltages = getattr(
            message,
            "voltages",
            None,
        )

        if voltages:
            first_voltage = voltages[0]

            if first_voltage not in {
                None,
                0,
                65535,
            }:
                updates["battery_v"] = round(
                    float(first_voltage) / 1000.0,
                    2,
                )

        battery_remaining = getattr(
            message,
            "battery_remaining",
            None,
        )

        if battery_remaining not in {
            None,
            -1,
        }:
            updates[
                "battery_remaining_pct"
            ] = int(
                battery_remaining
            )

        if updates:
            self._telemetry_store.update(
                **updates
            )

    def _process_sys_status(
        self,
        message: Any,
    ) -> None:
        updates: dict[str, Any] = {}

        voltage = getattr(
            message,
            "voltage_battery",
            None,
        )

        if voltage not in {
            None,
            -1,
            0,
            65535,
        }:
            updates["battery_v"] = round(
                float(voltage) / 1000.0,
                2,
            )

        battery_remaining = getattr(
            message,
            "battery_remaining",
            None,
        )

        if battery_remaining not in {
            None,
            -1,
        }:
            updates[
                "battery_remaining_pct"
            ] = int(
                battery_remaining
            )

        if updates:
            self._telemetry_store.update(
                **updates
            )

    def _process_gps_raw(
        self,
        message: Any,
    ) -> None:
        fix_type = int(
            getattr(
                message,
                "fix_type",
                0,
            )
        )

        eph = getattr(
            message,
            "eph",
            None,
        )

        hdop: float | None = None

        if (
            fix_type >= 2
            and eph not in {
                None,
                65535,
            }
        ):
            hdop = round(
                float(eph) / 100.0,
                2,
            )

        self._telemetry_store.update(
            gps_fix=fix_type,
            gps_fix_label=(
                GPS_FIX_LABELS.get(
                    fix_type,
                    f"UNKNOWN_{fix_type}",
                )
            ),
            gps_hdop=hdop,
        )

    def _process_mission_current(
        self,
        message: Any,
    ) -> None:
        waypoint_index = max(
            0,
            int(
                getattr(
                    message,
                    "seq",
                    0,
                )
            ),
        )

        snapshot = (
            self._telemetry_store.get_snapshot()
        )

        mission_complete = (
            snapshot.wp_total > 0
            and waypoint_index
            >= snapshot.wp_total
        )

        self._telemetry_store.update(
            wp_index=waypoint_index,
            mission_complete=mission_complete,
        )

    def _process_mission_count(
        self,
        message: Any,
    ) -> None:
        count = max(
            0,
            int(
                getattr(
                    message,
                    "count",
                    0,
                )
            ),
        )

        self._telemetry_store.update(
            wp_total=count,
            mission_loaded=count > 0,
            mission_complete=False,
        )

    def _process_navigation_output(
        self,
        message: Any,
    ) -> None:
        waypoint_distance = getattr(
            message,
            "wp_dist",
            None,
        )

        if waypoint_distance is None:
            return

        self._telemetry_store.update(
            wp_dist=round(
                float(waypoint_distance),
                1,
            )
        )

    def _process_mission_item_reached(
        self,
        message: Any,
    ) -> None:
        reached_sequence = max(
            0,
            int(
                getattr(
                    message,
                    "seq",
                    0,
                )
            ),
        )

        snapshot = (
            self._telemetry_store.get_snapshot()
        )

        mission_complete = (
            snapshot.wp_total > 0
            and reached_sequence
            >= snapshot.wp_total - 1
        )

        # MISSION_ITEM_REACHED reports the item that has just completed; it is
        # not the authoritative active target. Keep wp_index sourced only from
        # MISSION_CURRENT so the dashboard does not jump backwards in the field.
        self._telemetry_store.update(
            mission_complete=mission_complete,
        )


def utcnow_iso() -> str:
    """Return current UTC timestamp in protocol format."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
