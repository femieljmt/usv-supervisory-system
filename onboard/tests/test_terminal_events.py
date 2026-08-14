"""Tests for terminal event anti-spam behavior."""

from __future__ import annotations

from datetime import datetime
from unittest import TestCase

from onboard.terminal_events import TerminalEventManager


class CapturingLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)


class TerminalEventManagerTest(TestCase):

    def test_change_event_is_not_spammed(self) -> None:
        logger = CapturingLogger()
        manager = TerminalEventManager(
            logger=logger,
            clock=lambda: datetime(2026, 7, 10, 22, 0, 0),
        )
        self.assertFalse(
            manager.emit_change(
                "mqtt",
                "DOWN",
                "MQTT",
                "Broker terputus",
            )
        )
        self.assertFalse(
            manager.emit_change(
                "mqtt",
                "DOWN",
                "MQTT",
                "Broker terputus",
            )
        )
        self.assertTrue(
            manager.emit_change(
                "mqtt",
                "UP",
                "MQTT",
                "Broker terhubung",
            )
        )
        self.assertEqual(len(logger.lines), 1)

    def test_state_transition_prints_once_per_call(self) -> None:
        logger = CapturingLogger()
        manager = TerminalEventManager(
            logger=logger,
            clock=lambda: datetime(2026, 7, 10, 22, 0, 0),
        )
        manager.state_transition("NORMAL", "GCS_LOST")
        self.assertEqual(len(logger.lines), 1)
        self.assertIn("NORMAL -> GCS_LOST", logger.lines[0])
