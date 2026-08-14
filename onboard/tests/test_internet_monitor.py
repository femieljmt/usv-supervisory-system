"""Tests for onboard internet monitoring."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase

from onboard.internet_monitor import (
    InternetMonitor,
    parse_probe_target,
)
from onboard.protocol import InternetStatus


def make_settings():
    return SimpleNamespace(
        internet_probe_targets=(
            "1.1.1.1:443",
            "8.8.8.8:443",
        ),
        internet_probe_interval_seconds=2.0,
        internet_probe_timeout_seconds=1.0,
        internet_success_confirmations=2,
        internet_failure_confirmations=3,
    )


class SequenceProbe:
    def __init__(self, results: list[bool]) -> None:
        self._results = iter(results)

    def __call__(self, target, timeout) -> bool:
        return next(self._results)


class InternetMonitorTest(TestCase):

    def test_target_parser(self) -> None:
        target = parse_probe_target(
            "1.1.1.1:443"
        )

        self.assertEqual(target.host, "1.1.1.1")
        self.assertEqual(target.port, 443)

    def test_status_requires_confirmation(self) -> None:
        monitor = InternetMonitor(
            make_settings(),
            probe_function=SequenceProbe(
                [
                    True,
                    True,

                    False,
                    False,
                    
            False,
                    False,

            False,
            False,

            True,
                    True,
                ]
            ),
        )

        first = monitor.check_once()
        self.assertEqual(
            first.status,
            InternetStatus.UNKNOWN,
        )

        second = monitor.check_once()
        self.assertEqual(
            second.status,
            InternetStatus.AVAILABLE,
        )

        third = monitor.check_once()
        self.assertEqual(
            third.status,
            InternetStatus.AVAILABLE,
        )

        fourth = monitor.check_once()
        self.assertEqual(
            fourth.status,
            InternetStatus.AVAILABLE,
        )

        fifth = monitor.check_once()
        self.assertEqual(
            fifth.status,
            InternetStatus.UNAVAILABLE,
        )

        sixth = monitor.check_once()
        self.assertEqual(
            sixth.status,
            InternetStatus.UNAVAILABLE,
        )

        seventh = monitor.check_once()
        self.assertEqual(
            seventh.status,
            InternetStatus.AVAILABLE,
        )

    def test_one_successful_target_is_enough(self) -> None:
        calls: list[str] = []

        def probe(target, timeout) -> bool:
            calls.append(target.label)
            return target.host == "8.8.8.8"

        monitor = InternetMonitor(
            make_settings(),
            probe_function=probe,
        )

        monitor.check_once()
        snapshot = monitor.check_once()

        self.assertEqual(
            snapshot.status,
            InternetStatus.AVAILABLE,
        )
        self.assertEqual(
            snapshot.successful_target,
            "8.8.8.8:443",
        )
        self.assertIn(
            "1.1.1.1:443",
            calls,
        )
        self.assertIn(
            "8.8.8.8:443",
            calls,
        )
