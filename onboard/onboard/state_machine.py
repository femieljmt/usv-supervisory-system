"""Four-state supervisory state machine for Raspberry Pi USV.

State hanya ditentukan oleh:
- ketersediaan heartbeat Pixhawk;
- ketersediaan internet onboard;
- aktivitas pengiriman ulang yang benar-benar sedang berlangsung.

MQTT, broker, backend, ACK, dan jumlah buffer tidak menjadi pemicu
GCS_LOST. Semua kondisi tersebut tetap dicatat sebagai status komunikasi.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from onboard.protocol import InternetStatus, SupervisorState


@dataclass(frozen=True)
class StateInputs:
    """Current inputs used to determine supervisor state."""

    pixhawk_available: bool
    internet_status: InternetStatus
    recovery_active: bool


@dataclass(frozen=True)
class StateTransition:
    previous_state: SupervisorState
    current_state: SupervisorState
    changed: bool
    reason: str
    transitioned_at: str


def determine_supervisor_state(inputs: StateInputs) -> SupervisorState:
    """Determine state using the locked priority order."""

    if not inputs.pixhawk_available:
        return SupervisorState.PIXHAWK_LOST

    if inputs.internet_status == InternetStatus.UNAVAILABLE:
        return SupervisorState.GCS_LOST

    if inputs.recovery_active:
        return SupervisorState.RECOVERY

    return SupervisorState.NORMAL


class SupervisoryStateMachine:
    """Thread-safe owner of the current supervisor state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current_state = SupervisorState.PIXHAWK_LOST
        self._last_transition_at = utcnow_iso()

    @property
    def current_state(self) -> SupervisorState:
        with self._lock:
            return self._current_state

    @property
    def last_transition_at(self) -> str:
        with self._lock:
            return self._last_transition_at

    def evaluate(self, inputs: StateInputs) -> StateTransition:
        new_state = determine_supervisor_state(inputs)
        reason = transition_reason(inputs, new_state)

        with self._lock:
            previous_state = self._current_state
            changed = new_state != previous_state

            if changed:
                self._current_state = new_state
                self._last_transition_at = utcnow_iso()

            transitioned_at = self._last_transition_at

        return StateTransition(
            previous_state=previous_state,
            current_state=new_state,
            changed=changed,
            reason=reason,
            transitioned_at=transitioned_at,
        )


def transition_reason(
    inputs: StateInputs,
    state: SupervisorState,
) -> str:
    if state == SupervisorState.PIXHAWK_LOST:
        return "MAVLINK_UNAVAILABLE"

    if state == SupervisorState.GCS_LOST:
        return "ONBOARD_INTERNET_UNAVAILABLE"

    if state == SupervisorState.RECOVERY:
        return "REPLAY_DELIVERY_ACTIVE"

    if inputs.internet_status == InternetStatus.UNKNOWN:
        return "INTERNET_NOT_CONFIRMED_LOST"

    return "MAVLINK_AND_INTERNET_AVAILABLE"


def utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
