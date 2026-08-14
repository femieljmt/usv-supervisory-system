"""Download the flight-plan mission from Pixhawk using MAVLink."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from pymavlink import mavutil

from onboard.mission_plan import (
    MissionItem,
    MissionPlanStore,
)


LOGGER = logging.getLogger(__name__)


MISSION_TYPE = (
    mavutil.mavlink.MAV_MISSION_TYPE_MISSION
)


class MissionDownloader:
    """Reliable sequential MAVLink mission downloader."""

    def __init__(
        self,
        store: MissionPlanStore,
        *,
        request_timeout_seconds: float = 1.5,
        max_retries: int = 5,
        refresh_interval_seconds: float = 0.0,
        change_debounce_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._timeout = request_timeout_seconds
        self._max_retries = max_retries
        if refresh_interval_seconds < 0:
            raise ValueError("refresh_interval_seconds tidak boleh negatif")
        if change_debounce_seconds < 0:
            raise ValueError("change_debounce_seconds tidak boleh negatif")
        self._refresh_interval = float(refresh_interval_seconds)
        self._change_debounce = float(change_debounce_seconds)

        self._lock = threading.RLock()

        self._target_system = 0
        self._target_component = 0

        self._active = False
        self._requested_once = False

        self._expected_count: int | None = None
        self._expected_seq = 0
        self._items: dict[int, MissionItem] = {}

        self._pending_request = "NONE"
        self._last_request_at: float | None = None
        self._retry_count = 0
        self._opaque_id: int | None = None

        self._last_attempt_at: float | None = None
        self._last_completed_at: float | None = None
        self._refresh_due_at: float | None = None
        self._last_mission_id: int | None = None
        self._ignore_mission_ack_until = 0.0
        self._last_change_hint_at: float | None = None
        self._change_hint_cooldown = max(2.0, self._change_debounce)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def on_heartbeat(
        self,
        message: Any,
        connection: Any,
    ) -> None:
        """Start one mission download after autopilot heartbeat."""

        if (
            getattr(message, "type", None)
            == mavutil.mavlink.MAV_TYPE_GCS
        ):
            return

        target_system = _message_source_system(
            message
        )
        target_component = (
            _message_source_component(message)
        )

        if target_system <= 0:
            target_system = int(
                getattr(
                    connection,
                    "target_system",
                    0,
                )
            )

        if target_component <= 0:
            target_component = int(
                getattr(
                    connection,
                    "target_component",
                    1,
                )
            )

        with self._lock:
            changed_target = (
                target_system
                != self._target_system
                or target_component
                != self._target_component
            )

            self._target_system = target_system
            self._target_component = (
                target_component
            )

            if changed_target:
                self._requested_once = False

            should_request = (
                not self._requested_once
                and not self._active
                and self._target_system > 0
            )

        if should_request:
            self.request_download(
                connection,
                force=True,
            )

    def request_download(
        self,
        connection: Any,
        *,
        force: bool = False,
    ) -> bool:
        """Request the current flight-plan mission."""

        with self._lock:
            if self._target_system <= 0:
                return False

            if self._active and not force:
                return False

            self._active = True
            self._requested_once = True
            self._expected_count = None
            self._expected_seq = 0
            self._items.clear()
            self._retry_count = 0
            self._opaque_id = None
            self._last_attempt_at = time.monotonic()
            self._refresh_due_at = None

        self._send_request_list(connection)

        LOGGER.info(
            "Permintaan daftar waypoint dikirim "
            "ke Pixhawk system=%s component=%s",
            self._target_system,
            self._target_component,
        )

        return True

    def handle_message(
        self,
        message: Any,
        connection: Any,
    ) -> None:
        message_type = message.get_type()

        if message_type == "MISSION_COUNT":
            self._handle_count(
                message,
                connection,
            )

        elif message_type == "MISSION_ITEM_INT":
            self._handle_item(
                message,
                connection,
                integer_coordinates=True,
            )

        elif message_type == "MISSION_ITEM":
            self._handle_item(
                message,
                connection,
                integer_coordinates=False,
            )

        elif message_type == "MISSION_CURRENT":
            self._observe_mission_current(message)

        elif message_type == "MISSION_ACK":
            self._observe_mission_ack(message)

        elif message_type in {"MISSION_REQUEST", "MISSION_REQUEST_INT"}:
            self._observe_external_upload_request(message)

    def tick(self, connection: Any, *, allow_refresh: bool = True) -> None:
        """Retry active requests and periodically detect mission changes."""

        now = time.monotonic()
        with self._lock:
            active = self._active
            last_request_at = self._last_request_at

        if active and last_request_at is not None:
            timed_out = now - last_request_at >= self._timeout
            if timed_out:
                with self._lock:
                    if self._retry_count >= self._max_retries:
                        self._active = False
                        self._pending_request = "NONE"
                        self._last_request_at = None
                        error = (
                            "Mission download timeout setelah "
                            f"{self._retry_count} retry"
                        )
                        self._store.set_error(error)
                        LOGGER.warning(error)
                    else:
                        self._retry_count += 1
                        pending_request = self._pending_request
                        expected_seq = self._expected_seq
                        error = None

                if error is None:
                    LOGGER.warning(
                        "Mengulang mission request: type=%s seq=%s retry=%s",
                        pending_request,
                        expected_seq,
                        self._retry_count,
                    )
                    if pending_request == "LIST":
                        self._send_request_list(connection)
                    elif pending_request == "ITEM":
                        self._send_request_item(connection, expected_seq)
            return

        if not allow_refresh:
            return

        with self._lock:
            if self._active or self._target_system <= 0:
                return
            hinted = (
                self._refresh_due_at is not None
                and now >= self._refresh_due_at
            )
            upload_quiet = (
                self._last_change_hint_at is None
                or now - self._last_change_hint_at >= max(1.0, self._change_debounce)
            )
            periodic = (
                self._refresh_interval > 0
                and self._last_attempt_at is not None
                and now - self._last_attempt_at >= self._refresh_interval
                and upload_quiet
            )

        # Event hints provide fast updates. A quiet periodic verification is
        # retained as a field-reliability fallback; identical content is ignored
        # by MissionPlanStore and therefore is not printed or republished.
        if hinted or periodic:
            reason = "mission-change event" if hinted else "diagnostic periodic refresh"
            if self.request_download(connection):
                LOGGER.info("Mission plan refresh dimulai: %s", reason)

    def _observe_mission_current(self, message: Any) -> None:
        mission_id_raw = getattr(message, "mission_id", 0)
        try:
            mission_id = int(mission_id_raw or 0)
        except (TypeError, ValueError):
            mission_id = 0

        if mission_id <= 0:
            return

        with self._lock:
            previous = self._last_mission_id
            self._last_mission_id = mission_id

        if previous is not None and previous != mission_id:
            self._schedule_refresh("MISSION_CURRENT mission_id changed")

    def _observe_mission_ack(self, message: Any) -> None:
        """Treat an accepted ACK from the autopilot as upload completion.

        ACKs looped back from this downloader or sent by another GCS are ignored.
        """

        if not _is_flight_plan(message):
            return

        source_system = _message_source_system(message)
        source_component = _message_source_component(message)
        if (
            source_system != self._target_system
            or (
                source_component > 0
                and self._target_component > 0
                and source_component != self._target_component
            )
        ):
            return

        ack_type = int(getattr(message, "type", -1))
        if ack_type != mavutil.mavlink.MAV_MISSION_ACCEPTED:
            return

        now = time.monotonic()
        with self._lock:
            if now < self._ignore_mission_ack_until:
                return

        self._schedule_refresh("external mission upload accepted")

    def _observe_external_upload_request(self, message: Any) -> None:
        """Notice an autopilot requesting mission items from another GCS.

        During an external upload ArduPilot emits MISSION_REQUEST(_INT) for
        every item. This signal is more reliable in the field than depending on
        a single final MISSION_ACK packet. The due time is extended on every
        request so the verification download starts after the upload settles.
        """

        if not _is_flight_plan(message):
            return
        source_system = _message_source_system(message)
        if self._target_system > 0 and source_system not in {0, self._target_system}:
            return
        self._schedule_refresh(
            "external mission upload activity",
            extend=True,
        )

    def _schedule_refresh(self, reason: str, *, extend: bool = False) -> None:
        now = time.monotonic()
        due = now + self._change_debounce
        with self._lock:
            # Never discard a change hint merely because a verification download
            # is active. It will be processed immediately after that download.
            if extend or self._refresh_due_at is None:
                self._refresh_due_at = due
            else:
                self._refresh_due_at = min(self._refresh_due_at, due)

            should_log = (
                self._last_change_hint_at is None
                or now - self._last_change_hint_at >= self._change_hint_cooldown
            )
            if should_log:
                self._last_change_hint_at = now

        if should_log:
            LOGGER.info("Perubahan mission terdeteksi: %s", reason)

    def _handle_count(
        self,
        message: Any,
        connection: Any,
    ) -> None:
        if not self._message_is_expected(message):
            return

        if not _is_flight_plan(message):
            return

        with self._lock:
            if not self._active:
                return

            count = max(
                0,
                int(message.count),
            )

            self._expected_count = count
            self._expected_seq = 0
            self._items.clear()
            self._retry_count = 0

            opaque_id = getattr(
                message,
                "opaque_id",
                None,
            )

            self._opaque_id = (
                int(opaque_id)
                if opaque_id is not None
                else None
            )

        LOGGER.info(
            "Jumlah mission item diterima: %s",
            count,
        )

        if count == 0:
            self._finish_download(
                connection,
                [],
            )
            return

        self._send_request_item(
            connection,
            0,
        )

    def _handle_item(
        self,
        message: Any,
        connection: Any,
        *,
        integer_coordinates: bool,
    ) -> None:
        if not self._message_is_expected(message):
            return

        if not _is_flight_plan(message):
            return

        with self._lock:
            if (
                not self._active
                or self._expected_count is None
            ):
                return

            seq = int(message.seq)

            if seq != self._expected_seq:
                expected = self._expected_seq
            else:
                expected = None

        if expected is not None:
            LOGGER.warning(
                "Mission item di luar urutan: "
                "received=%s expected=%s",
                seq,
                expected,
            )

            self._send_request_item(
                connection,
                expected,
            )
            return

        item = _parse_mission_item(
            message,
            integer_coordinates=(
                integer_coordinates
            ),
        )

        with self._lock:
            self._items[item.seq] = item
            self._expected_seq += 1
            self._retry_count = 0

            complete = (
                self._expected_seq
                >= self._expected_count
            )

            next_seq = self._expected_seq

            items = [
                self._items[index]
                for index in sorted(self._items)
            ]

        if complete:
            self._finish_download(
                connection,
                items,
            )
        else:
            self._send_request_item(
                connection,
                next_seq,
            )

    def _finish_download(
        self,
        connection: Any,
        items: list[MissionItem],
    ) -> None:
        snapshot = self._store.set_complete(
            items,
            opaque_id=self._opaque_id,
        )

        # Ignore any local/MAVProxy loopback of the ACK sent by this downloader.
        with self._lock:
            self._ignore_mission_ack_until = time.monotonic() + 2.0

        self._send_ack(connection)

        with self._lock:
            self._active = False
            self._pending_request = "NONE"
            self._last_request_at = None
            self._retry_count = 0
            self._last_completed_at = time.monotonic()
            if self._opaque_id is not None and self._opaque_id > 0:
                self._last_mission_id = self._opaque_id

        LOGGER.info(
            "Mission plan selesai diunduh: "
            "total=%s executable=%s revision=%s",
            snapshot.total_count,
            snapshot.executable_count,
            snapshot.revision,
        )

    def _send_request_list(
        self,
        connection: Any,
    ) -> None:
        try:
            connection.mav.mission_request_list_send(
                self._target_system,
                self._target_component,
                MISSION_TYPE,
            )
        except TypeError:
            connection.mav.mission_request_list_send(
                self._target_system,
                self._target_component,
            )

        with self._lock:
            self._pending_request = "LIST"
            self._last_request_at = (
                time.monotonic()
            )

    def _send_request_item(
        self,
        connection: Any,
        seq: int,
    ) -> None:
        try:
            connection.mav.mission_request_int_send(
                self._target_system,
                self._target_component,
                int(seq),
                MISSION_TYPE,
            )
        except TypeError:
            connection.mav.mission_request_int_send(
                self._target_system,
                self._target_component,
                int(seq),
            )

        with self._lock:
            self._pending_request = "ITEM"
            self._last_request_at = (
                time.monotonic()
            )

    def _send_ack(
        self,
        connection: Any,
    ) -> None:
        accepted = (
            mavutil.mavlink.MAV_MISSION_ACCEPTED
        )

        try:
            connection.mav.mission_ack_send(
                self._target_system,
                self._target_component,
                accepted,
                MISSION_TYPE,
            )
        except TypeError:
            connection.mav.mission_ack_send(
                self._target_system,
                self._target_component,
                accepted,
            )

    def _message_is_expected(
        self,
        message: Any,
    ) -> bool:
        source_system = _message_source_system(
            message
        )
        source_component = (
            _message_source_component(message)
        )

        if (
            source_system > 0
            and self._target_system > 0
            and source_system != self._target_system
        ):
            return False

        if (
            source_component > 0
            and self._target_component > 0
            and source_component
            != self._target_component
        ):
            return False

        return True


def _parse_mission_item(
    message: Any,
    *,
    integer_coordinates: bool,
) -> MissionItem:
    seq = int(message.seq)
    command = int(message.command)
    frame = int(message.frame)

    command_name = _enum_name(
        "MAV_CMD",
        command,
        f"MAV_CMD_{command}",
    )

    frame_name = _enum_name(
        "MAV_FRAME",
        frame,
        f"MAV_FRAME_{frame}",
    )

    is_home = seq == 0

    positional = (
        is_home
        or command_name.startswith(
            "MAV_CMD_NAV_"
        )
    )

    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None

    if positional:
        raw_x = getattr(message, "x", None)
        raw_y = getattr(message, "y", None)
        raw_z = getattr(message, "z", None)

        if (
            raw_x is not None
            and raw_y is not None
        ):
            if integer_coordinates:
                latitude = round(
                    float(raw_x) / 1e7,
                    7,
                )
                longitude = round(
                    float(raw_y) / 1e7,
                    7,
                )
            else:
                latitude = round(
                    float(raw_x),
                    7,
                )
                longitude = round(
                    float(raw_y),
                    7,
                )

        if raw_z is not None:
            altitude = round(
                float(raw_z),
                2,
            )

    return MissionItem(
        seq=seq,
        command=command,
        command_name=command_name,
        frame=frame,
        frame_name=frame_name,
        is_home=is_home,
        current=bool(
            getattr(message, "current", 0)
        ),
        autocontinue=bool(
            getattr(
                message,
                "autocontinue",
                0,
            )
        ),
        param1=float(
            getattr(message, "param1", 0.0)
        ),
        param2=float(
            getattr(message, "param2", 0.0)
        ),
        param3=float(
            getattr(message, "param3", 0.0)
        ),
        param4=float(
            getattr(message, "param4", 0.0)
        ),
        lat=latitude,
        lon=longitude,
        alt=altitude,
    )


def _enum_name(
    enum_name: str,
    value: int,
    fallback: str,
) -> str:
    enum_values = (
        mavutil.mavlink.enums.get(
            enum_name,
            {},
        )
    )

    enum_entry = enum_values.get(value)

    if enum_entry is None:
        return fallback

    return str(enum_entry.name)


def _message_source_system(
    message: Any,
) -> int:
    try:
        return int(message.get_srcSystem())
    except (AttributeError, TypeError, ValueError):
        return 0


def _message_source_component(
    message: Any,
) -> int:
    try:
        return int(
            message.get_srcComponent()
        )
    except (AttributeError, TypeError, ValueError):
        return 0


def _is_flight_plan(
    message: Any,
) -> bool:
    mission_type = getattr(
        message,
        "mission_type",
        MISSION_TYPE,
    )

    return int(mission_type) == MISSION_TYPE
