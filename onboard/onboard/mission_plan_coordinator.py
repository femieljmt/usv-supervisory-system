"""Mission-plan logging, terminal display, and MQTT publication."""

from __future__ import annotations

import logging
import threading

from onboard.mission_plan import (
    MissionPlanLogger,
    MissionPlanStore,
)
from onboard.mqtt_manager import MQTTManager
from onboard.terminal_display import (
    TERMINAL_LOGGER_NAME,
    format_mission_plan,
)


LOGGER = logging.getLogger(__name__)

TERMINAL_LOGGER = logging.getLogger(
    TERMINAL_LOGGER_NAME
)


class MissionPlanCoordinator:
    """Process every new complete mission-plan revision once."""

    def __init__(
        self,
        *,
        vehicle_id: str,
        session_id: str,
        store: MissionPlanStore,
        logger: MissionPlanLogger,
        mqtt_manager: MQTTManager,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError(
                "poll_interval_seconds harus lebih besar dari 0"
            )

        self._vehicle_id = vehicle_id
        self._session_id = session_id
        self._store = store
        self._logger = logger
        self._mqtt_manager = mqtt_manager
        self._poll_interval = (
            poll_interval_seconds
        )

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._last_logged_revision = 0
        self._last_published_revision = 0

    @property
    def running(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
        )

    def start(self) -> None:
        if self.running:
            return

        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run,
            name="mission-plan-coordinator",
            daemon=True,
        )

        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=5.0)

        LOGGER.info(
            "Mission plan coordinator dihentikan"
        )

    def process_once(self) -> bool:
        """Log, display, and publish the latest complete revision."""

        snapshot = self._store.get_snapshot()

        if (
            not snapshot.complete
            or snapshot.revision <= 0
        ):
            return False

        payload = snapshot.to_payload(
            vehicle_id=self._vehicle_id,
            session_id=self._session_id,
        )

        if (
            snapshot.revision
            != self._last_logged_revision
        ):
            log_path = self._logger.write(
                payload
            )

            TERMINAL_LOGGER.info(
                format_mission_plan(
                    snapshot,
                    is_update=(self._last_logged_revision > 0),
                )
            )

            LOGGER.info(
                "Mission plan dicatat: "
                "revision=%s path=%s",
                snapshot.revision,
                log_path,
            )

            self._last_logged_revision = (
                snapshot.revision
            )

        if (
            snapshot.revision
            != self._last_published_revision
            and self._mqtt_manager.is_connected
        ):
            result = (
                self._mqtt_manager.publish_waypoints(
                    payload
                )
            )

            if result.accepted:
                self._last_published_revision = (
                    snapshot.revision
                )

                LOGGER.info(
                    "Mission plan dipublikasikan: "
                    "revision=%s topic=%s",
                    snapshot.revision,
                    result.topic,
                )

        return True

    def _run(self) -> None:
        LOGGER.info(
            "Mission plan coordinator dimulai"
        )

        while not self._stop_event.is_set():
            try:
                self.process_once()

            except Exception:
                LOGGER.exception(
                    "Gagal memproses mission plan"
                )

            self._stop_event.wait(
                self._poll_interval
            )

        LOGGER.info(
            "Mission plan coordinator worker selesai"
        )
