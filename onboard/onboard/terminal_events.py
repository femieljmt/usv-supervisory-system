"""Structured, anti-spam events for the onboard terminal.

Terminal events are intentionally separate from the complete runtime log.
Only operational transitions that help an observer understand the
supervisory mechanism are shown on screen.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from onboard.terminal_display import (
    TERMINAL_LOGGER_NAME,
    format_terminal_event,
)


@dataclass(frozen=True)
class TerminalEvent:
    level: str
    category: str
    message: str
    occurred_at: datetime


Clock = Callable[[], datetime]


class TerminalEventManager:
    """Emit concise terminal events while suppressing duplicates."""

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger(
            TERMINAL_LOGGER_NAME
        )
        self._clock = clock or datetime.now
        self._lock = threading.RLock()
        self._last_values: dict[str, object] = {}

    def emit(
        self,
        category: str,
        message: str,
        *,
        level: str = "INFO",
    ) -> None:
        """Emit one event without deduplication."""

        event = TerminalEvent(
            level=level.upper(),
            category=category.upper(),
            message=message,
            occurred_at=self._clock(),
        )
        self._logger.info(format_terminal_event(event))

    def emit_change(
        self,
        key: str,
        value: object,
        category: str,
        message: str,
        *,
        level: str = "INFO",
        emit_initial: bool = False,
    ) -> bool:
        """Emit only when *value* changes for the supplied key."""

        with self._lock:
            known = key in self._last_values
            previous = self._last_values.get(key)
            self._last_values[key] = value

        if known and previous == value:
            return False

        if not known and not emit_initial:
            return False

        self.emit(
            category,
            message,
            level=level,
        )
        return True

    def remember(self, key: str, value: object) -> None:
        """Set a deduplication baseline without producing output."""

        with self._lock:
            self._last_values[key] = value

    def state_transition(
        self,
        previous: str,
        current: str,
    ) -> None:
        if previous == current:
            return

        self.emit(
            "STATE",
            f"{previous} -> {current}",
        )
