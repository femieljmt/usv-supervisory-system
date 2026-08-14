"""Persistent outbox delivery and stable GCS-loss recovery.

The synchronizer uses a controlled ACK window. Only records created while
``supervisor_state == GCS_LOST`` participate in a supervisory RECOVERY
session. MQTT/backend failures while internet is available remain ordinary
pending delivery and never change the supervisory state.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from onboard.ack_manager import AckManager
from onboard.config import Settings
from onboard.mqtt_manager import MQTTManager, PublishResult
from onboard.persistent_outbox import OutboxRecord, PersistentOutbox
from onboard.protocol import (
    ACKStatus,
    DeliveryType,
    MQTTConnectionStatus,
    MQTTPublishStatus,
    SupervisorState,
    SyncStatus,
    validate_telemetry_payload,
)


LOGGER = logging.getLogger(__name__)

SYNC_IDLE = "IDLE"
SYNC_DELIVERY_DISABLED = "DELIVERY_DISABLED"
SYNC_MQTT_DISCONNECTED = "MQTT_DISCONNECTED"
SYNC_WAITING_ACK = "WAITING_ACK"
SYNC_PUBLISHED_LIVE = "PUBLISHED_LIVE"
SYNC_PUBLISHED_REPLAY = "PUBLISHED_REPLAY"
SYNC_BURST_PUBLISHED = "BURST_PUBLISHED"
SYNC_ACK_CONFIRMED = "ACK_CONFIRMED"
SYNC_ACK_TIMEOUT = "ACK_TIMEOUT"
SYNC_PUBLISH_REJECTED = "PUBLISH_REJECTED"
SYNC_RECORD_MISSING = "RECORD_MISSING"

Identity = tuple[str, str, int]
DeliveryEnabledProvider = Callable[[], bool]
MonotonicClock = Callable[[], float]


@dataclass(frozen=True)
class ActiveDelivery:
    vehicle_id: str
    session_id: str
    seq_id: int
    deadline_monotonic: float
    replay: bool

    @property
    def identity(self) -> Identity:
        return self.vehicle_id, self.session_id, self.seq_id


@dataclass
class RecoverySession:
    targets: set[Identity]
    total_records: int
    total_batches: int
    current_batch: set[Identity] = field(default_factory=set)
    current_batch_number: int = 0
    completed_records: int = 0
    started: bool = False


@dataclass(frozen=True)
class SynchronizerStepResult:
    action: str
    vehicle_id: str | None = None
    session_id: str | None = None
    seq_id: int | None = None
    replay: bool = False
    mqtt_result: PublishResult | None = None
    published_count: int = 0
    confirmed_count: int = 0
    timeout_count: int = 0
    in_flight_count: int = 0
    replay_in_flight_count: int = 0


@dataclass(frozen=True)
class SynchronizerSnapshot:
    last_action: str
    in_flight_count: int
    replay_in_flight_count: int
    recovery_active: bool
    window_size: int
    recovery_total: int = 0
    recovery_completed: int = 0
    recovery_batch: int = 0
    recovery_batches: int = 0


class OutboxSynchronizer:
    """Deliver outbox records and manage a stable recovery session."""

    def __init__(
        self,
        settings: Settings,
        outbox: PersistentOutbox,
        mqtt_manager: MQTTManager,
        ack_manager: AckManager,
        *,
        delivery_enabled_provider: DeliveryEnabledProvider | None = None,
        monotonic_clock: MonotonicClock | None = None,
        poll_interval_seconds: float = 0.05,
        terminal_events: object | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds harus lebih besar dari 0")

        window_size = int(getattr(settings, "sync_batch_size", 1))
        if window_size <= 0:
            raise ValueError("sync_batch_size harus lebih besar dari 0")

        self._settings = settings
        self._window_size = window_size
        self._outbox = outbox
        self._mqtt_manager = mqtt_manager
        self._ack_manager = ack_manager
        self._delivery_enabled_provider = (
            delivery_enabled_provider or (lambda: True)
        )
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._poll_interval_seconds = poll_interval_seconds
        self._terminal_events = terminal_events

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._active_deliveries: dict[Identity, ActiveDelivery] = {}
        self._recovery_session: RecoverySession | None = None
        self._recovery_active = False
        self._last_action = SYNC_IDLE
        self._delivery_was_enabled: bool | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def window_size(self) -> int:
        return self._window_size

    @property
    def active_identities(self) -> tuple[Identity, ...]:
        with self._lock:
            return tuple(self._active_deliveries)

    @property
    def active_identity(self) -> Identity | None:
        identities = self.active_identities
        return identities[0] if identities else None

    @property
    def in_flight(self) -> bool:
        return self.in_flight_count > 0

    @property
    def in_flight_count(self) -> int:
        with self._lock:
            return len(self._active_deliveries)

    @property
    def replay_in_flight_count(self) -> int:
        with self._lock:
            return sum(item.replay for item in self._active_deliveries.values())

    @property
    def last_action(self) -> str:
        with self._lock:
            return self._last_action

    @property
    def recovery_active(self) -> bool:
        with self._lock:
            return self._recovery_active

    @property
    def delivery_path_ready(self) -> bool:
        return self._delivery_enabled() and self._mqtt_manager.is_connected

    def snapshot(self) -> SynchronizerSnapshot:
        with self._lock:
            session = self._recovery_session
            return SynchronizerSnapshot(
                last_action=self._last_action,
                in_flight_count=len(self._active_deliveries),
                replay_in_flight_count=sum(
                    item.replay for item in self._active_deliveries.values()
                ),
                recovery_active=self._recovery_active,
                window_size=self._window_size,
                recovery_total=(session.total_records if session else 0),
                recovery_completed=(
                    session.completed_records if session else 0
                ),
                recovery_batch=(
                    session.current_batch_number if session else 0
                ),
                recovery_batches=(session.total_batches if session else 0),
            )

    def start(self) -> None:
        if self.running:
            return

        self._outbox.initialize()
        recovered = self._outbox.reset_in_flight_to_pending()
        if recovered:
            LOGGER.info(
                "%s record IN_FLIGHT dipulihkan menjadi PENDING",
                recovered,
            )

        with self._lock:
            self._active_deliveries.clear()
            self._recovery_session = None
            self._recovery_active = False
            self._last_action = SYNC_IDLE
            self._delivery_was_enabled = None

        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="outbox-synchronizer",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info(
            "Outbox synchronizer dimulai; batch/window=%s",
            self._window_size,
        )

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._set_recovery_active(False)
        LOGGER.info("Outbox synchronizer dihentikan")

    def notify_new_record(self) -> None:
        self._wake_event.set()

    def process_once(self) -> SynchronizerStepResult:
        confirmed_count, timeout_count = self._reconcile_active_deliveries()
        self._finish_batch_if_complete()
        self._finish_recovery_if_complete()

        delivery_enabled = self._delivery_enabled()
        if not delivery_enabled:
            self._pause_active_deliveries()
            self._delivery_was_enabled = False
            self._set_recovery_active(False)
            return self._save_result(
                self._result(
                    SYNC_DELIVERY_DISABLED,
                    confirmed_count=confirmed_count,
                    timeout_count=timeout_count,
                )
            )

        if not self._mqtt_manager.is_connected:
            self._pause_active_deliveries()
            self._delivery_was_enabled = True
            self._set_recovery_active(False)
            return self._save_result(
                self._result(
                    SYNC_MQTT_DISCONNECTED,
                    confirmed_count=confirmed_count,
                    timeout_count=timeout_count,
                )
            )

        self._delivery_was_enabled = True
        self._ensure_recovery_session()

        if timeout_count > 0:
            self._refresh_recovery_state()
            return self._save_result(
                self._result(
                    SYNC_ACK_TIMEOUT,
                    confirmed_count=confirmed_count,
                    timeout_count=timeout_count,
                )
            )

        if self._recovery_session is not None:
            published = self._fill_recovery_batch()
        else:
            published = self._fill_normal_window()

        self._refresh_recovery_state()

        if published:
            first = published[0]
            action = (
                first.action
                if len(published) == 1
                else SYNC_BURST_PUBLISHED
            )
            return self._save_result(
                self._result(
                    action,
                    vehicle_id=first.vehicle_id,
                    session_id=first.session_id,
                    seq_id=first.seq_id,
                    replay=any(item.replay for item in published),
                    mqtt_result=first.mqtt_result,
                    published_count=len(published),
                    confirmed_count=confirmed_count,
                )
            )

        if confirmed_count > 0:
            return self._save_result(
                self._result(
                    SYNC_ACK_CONFIRMED,
                    confirmed_count=confirmed_count,
                )
            )

        if self.in_flight_count > 0:
            return self._save_result(self._result(SYNC_WAITING_ACK))

        self._refresh_recovery_state()
        return self._save_result(self._result(SYNC_IDLE))

    def _ensure_recovery_session(self) -> None:
        with self._lock:
            if self._recovery_session is not None:
                return

        identities = set(
            self._outbox.list_identities_by_supervisor_state(
                SupervisorState.GCS_LOST.value
            )
        )
        if not identities:
            return

        session = RecoverySession(
            targets=identities,
            total_records=len(identities),
            total_batches=math.ceil(len(identities) / self._window_size),
        )
        with self._lock:
            self._recovery_session = session

    def _fill_recovery_batch(self) -> list[SynchronizerStepResult]:
        with self._lock:
            session = self._recovery_session
        if session is None:
            return []

        remaining = {
            identity
            for identity in session.targets
            if self._outbox.contains(*identity)
        }
        if not remaining:
            self._finish_recovery_if_complete()
            return []

        # A recovery batch is completed as one logical group. Do not start
        # the next group until every identity in the current group is ACKed.
        if session.current_batch:
            batch_remaining = {
                identity
                for identity in session.current_batch
                if self._outbox.contains(*identity)
            }
            if batch_remaining:
                available_slots = (
                    self._window_size - self.in_flight_count
                )
                if available_slots <= 0:
                    return []
                records = self._outbox.get_pending_by_identities(
                    batch_remaining,
                    limit=available_slots,
                )
                return [
                    self._publish_record(record, replay=True)
                    for record in records
                    if self.delivery_path_ready
                ]
            self._finish_batch_if_complete()
            return []

        records = self._outbox.get_pending_by_identities(
            remaining,
            limit=self._window_size,
        )
        if not records:
            return []

        session.current_batch_number += 1
        session.current_batch = {
            (record.vehicle_id, record.session_id, record.seq_id)
            for record in records
        }
        if not session.started:
            session.started = True
            self._emit(
                "RECOVERY",
                f"Sinkronisasi dimulai: {session.total_records} records "
                f"dalam {session.total_batches} batch",
            )

        results: list[SynchronizerStepResult] = []
        for record in records:
            if not self.delivery_path_ready:
                break
            results.append(self._publish_record(record, replay=True))
        return results

    def _fill_normal_window(self) -> list[SynchronizerStepResult]:
        available_slots = self._window_size - self.in_flight_count
        if available_slots <= 0:
            return []

        records = self._outbox.get_pending_oldest(limit=available_slots)
        results: list[SynchronizerStepResult] = []
        for record in records:
            if not self.delivery_path_ready:
                break
            # Ordinary MQTT/backend pending data remains LIVE. Only records
            # created during GCS_LOST are supervisory REPLAY data.
            replay = (
                record.payload.get("supervisor_state")
                == SupervisorState.GCS_LOST.value
            )
            if replay:
                # The next cycle will create a recovery session and handle it
                # in deterministic batches.
                self._ensure_recovery_session()
                break
            result = self._publish_record(record, replay=False)
            results.append(result)
            if result.action in {SYNC_PUBLISH_REJECTED, SYNC_RECORD_MISSING}:
                break
        return results

    def _publish_record(
        self,
        record: OutboxRecord,
        *,
        replay: bool,
    ) -> SynchronizerStepResult:
        increment_retry = record.last_attempt_at is not None
        retry_count = record.retry_count + (1 if increment_retry else 0)

        payload = dict(record.payload)
        payload.update(
            {
                "delivery_type": (
                    DeliveryType.REPLAY.value
                    if replay
                    else DeliveryType.LIVE.value
                ),
                "mqtt_connection_status": MQTTConnectionStatus.CONNECTED.value,
                "mqtt_publish_status": MQTTPublishStatus.PUBLISHED.value,
                "ack_status": ACKStatus.PENDING.value,
                "ack_latency_ms": None,
                "retry_count": retry_count,
                "buffer_count": self._outbox.count(),
                "sync_status": (
                    SyncStatus.SYNCING.value
                    if replay
                    else SyncStatus.IDLE.value
                ),
            }
        )
        validate_telemetry_payload(payload)

        marked = self._outbox.mark_attempt(
            record.vehicle_id,
            record.session_id,
            record.seq_id,
            increment_retry=increment_retry,
        )
        if not marked:
            return SynchronizerStepResult(
                action=SYNC_RECORD_MISSING,
                vehicle_id=record.vehicle_id,
                session_id=record.session_id,
                seq_id=record.seq_id,
                replay=replay,
            )

        mqtt_result = self._mqtt_manager.publish_telemetry(payload)
        if not mqtt_result.accepted:
            self._outbox.mark_pending(
                record.vehicle_id,
                record.session_id,
                record.seq_id,
            )
            LOGGER.warning(
                "Publish MQTT ditolak; record tetap PENDING: %s/%s/%s rc=%s",
                record.vehicle_id,
                record.session_id,
                record.seq_id,
                mqtt_result.rc,
            )
            return SynchronizerStepResult(
                action=SYNC_PUBLISH_REJECTED,
                vehicle_id=record.vehicle_id,
                session_id=record.session_id,
                seq_id=record.seq_id,
                replay=replay,
                mqtt_result=mqtt_result,
            )

        active = ActiveDelivery(
            vehicle_id=record.vehicle_id,
            session_id=record.session_id,
            seq_id=record.seq_id,
            deadline_monotonic=(
                self._monotonic_clock()
                + self._settings.ack_timeout_seconds
            ),
            replay=replay,
        )
        with self._lock:
            self._active_deliveries[active.identity] = active

        LOGGER.info(
            "Menunggu ACK: %s/%s/%s delivery=%s",
            record.vehicle_id,
            record.session_id,
            record.seq_id,
            "REPLAY" if replay else "LIVE",
        )
        return SynchronizerStepResult(
            action=(SYNC_PUBLISHED_REPLAY if replay else SYNC_PUBLISHED_LIVE),
            vehicle_id=record.vehicle_id,
            session_id=record.session_id,
            seq_id=record.seq_id,
            replay=replay,
            mqtt_result=mqtt_result,
            published_count=1,
        )

    def _reconcile_active_deliveries(self) -> tuple[int, int]:
        now = self._monotonic_clock()
        confirmed_count = 0
        timeout_count = 0
        with self._lock:
            active_items = list(self._active_deliveries.items())

        for identity, active in active_items:
            if not self._outbox.contains(*identity):
                with self._lock:
                    self._active_deliveries.pop(identity, None)
                confirmed_count += 1
                continue

            if now < active.deadline_monotonic:
                continue

            self._ack_manager.mark_timeout(*identity)
            with self._lock:
                self._active_deliveries.pop(identity, None)
            timeout_count += 1
            LOGGER.warning(
                "ACK timeout; record kembali PENDING: %s/%s/%s",
                *identity,
            )

        return confirmed_count, timeout_count

    def _finish_batch_if_complete(self) -> None:
        with self._lock:
            session = self._recovery_session
        if session is None or not session.current_batch:
            return

        if any(self._outbox.contains(*identity) for identity in session.current_batch):
            return

        batch_size = len(session.current_batch)
        session.completed_records += batch_size
        self._emit(
            "BATCH",
            f"Batch {session.current_batch_number}/{session.total_batches}: "
            f"{batch_size} records ACK",
        )
        session.current_batch.clear()

    def _finish_recovery_if_complete(self) -> None:
        with self._lock:
            session = self._recovery_session
        if session is None:
            return

        remaining = sum(
            self._outbox.contains(*identity)
            for identity in session.targets
        )
        if remaining > 0:
            return

        if session.started:
            self._emit(
                "RECOVERY",
                f"Sinkronisasi selesai: {session.total_records}/"
                f"{session.total_records} records",
            )
        with self._lock:
            self._recovery_session = None
            self._recovery_active = False

    def _pause_active_deliveries(self) -> None:
        with self._lock:
            identities = set(self._active_deliveries)
            self._active_deliveries.clear()
        if identities:
            self._outbox.reset_identities_to_pending(identities)

    def _refresh_recovery_state(self) -> None:
        with self._lock:
            session = self._recovery_session
            active = bool(
                session is not None
                and session.started
                and self.delivery_path_ready
            )
            self._recovery_active = active

    def _delivery_enabled(self) -> bool:
        try:
            return bool(self._delivery_enabled_provider())
        except Exception:
            LOGGER.exception("delivery_enabled_provider mengalami error")
            return False

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.process_once()
            except Exception:
                self._set_recovery_active(False)
                LOGGER.exception("Kesalahan pada outbox synchronizer")

            self._wake_event.wait(timeout=self._poll_interval_seconds)
            self._wake_event.clear()
        LOGGER.info("Outbox synchronizer worker selesai")

    def _set_recovery_active(self, active: bool) -> None:
        with self._lock:
            self._recovery_active = bool(active)

    def _emit(
        self,
        category: str,
        message: str,
        *,
        level: str = "INFO",
    ) -> None:
        manager = self._terminal_events
        if manager is not None and hasattr(manager, "emit"):
            manager.emit(category, message, level=level)

    def _result(self, action: str, **kwargs) -> SynchronizerStepResult:
        return SynchronizerStepResult(
            action=action,
            in_flight_count=self.in_flight_count,
            replay_in_flight_count=self.replay_in_flight_count,
            **kwargs,
        )

    def _save_result(
        self,
        result: SynchronizerStepResult,
    ) -> SynchronizerStepResult:
        with self._lock:
            self._last_action = result.action
        return result
