"""Tests for the locked four-state supervisory state machine."""

from __future__ import annotations

from unittest import TestCase

from onboard.protocol import InternetStatus, SupervisorState
from onboard.state_machine import (
    StateInputs,
    SupervisoryStateMachine,
    determine_supervisor_state,
)


class StateMachineTest(TestCase):

    def test_only_four_states_are_defined(self) -> None:
        self.assertEqual(
            {state.value for state in SupervisorState},
            {
                "NORMAL",
                "GCS_LOST",
                "RECOVERY",
                "PIXHAWK_LOST",
            },
        )

    def test_pixhawk_lost_has_highest_priority(self) -> None:
        state = determine_supervisor_state(
            StateInputs(
                pixhawk_available=False,
                internet_status=InternetStatus.UNAVAILABLE,
                recovery_active=True,
            )
        )
        self.assertEqual(state, SupervisorState.PIXHAWK_LOST)

    def test_only_confirmed_internet_loss_creates_gcs_lost(self) -> None:
        state = determine_supervisor_state(
            StateInputs(
                pixhawk_available=True,
                internet_status=InternetStatus.UNAVAILABLE,
                recovery_active=False,
            )
        )
        self.assertEqual(state, SupervisorState.GCS_LOST)

    def test_unknown_internet_is_not_gcs_lost(self) -> None:
        state = determine_supervisor_state(
            StateInputs(
                pixhawk_available=True,
                internet_status=InternetStatus.UNKNOWN,
                recovery_active=False,
            )
        )
        self.assertEqual(state, SupervisorState.NORMAL)

    def test_mqtt_or_buffer_condition_alone_remains_normal(self) -> None:
        # MQTT dan buffer tidak menjadi input state machine. Bila internet
        # onboard belum dinyatakan hilang dan replay belum aktif, state NORMAL.
        state = determine_supervisor_state(
            StateInputs(
                pixhawk_available=True,
                internet_status=InternetStatus.AVAILABLE,
                recovery_active=False,
            )
        )
        self.assertEqual(state, SupervisorState.NORMAL)

    def test_recovery_requires_actual_replay_activity(self) -> None:
        state = determine_supervisor_state(
            StateInputs(
                pixhawk_available=True,
                internet_status=InternetStatus.AVAILABLE,
                recovery_active=True,
            )
        )
        self.assertEqual(state, SupervisorState.RECOVERY)

    def test_recovery_ends_when_activity_stops(self) -> None:
        machine = SupervisoryStateMachine()

        active = machine.evaluate(
            StateInputs(
                pixhawk_available=True,
                internet_status=InternetStatus.AVAILABLE,
                recovery_active=True,
            )
        )
        self.assertEqual(active.current_state, SupervisorState.RECOVERY)

        stopped = machine.evaluate(
            StateInputs(
                pixhawk_available=True,
                internet_status=InternetStatus.AVAILABLE,
                recovery_active=False,
            )
        )
        self.assertEqual(stopped.current_state, SupervisorState.NORMAL)
