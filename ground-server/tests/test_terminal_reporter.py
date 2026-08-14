"""Tests for concise server terminal reporting state."""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from server.terminal_logging import ServerTerminalReporter


class TerminalReporterTest(TestCase):
    def test_mqtt_duplicate_status_is_suppressed(self) -> None:
        reporter = ServerTerminalReporter(summary_interval=10)
        with patch.object(reporter, "event") as event:
            reporter.mqtt_status(True)
            reporter.mqtt_status(True)
            reporter.mqtt_status(False)
            reporter.mqtt_status(False)
        self.assertEqual(event.call_count, 2)

    def test_telemetry_summary_is_emitted_per_interval(self) -> None:
        reporter = ServerTerminalReporter(summary_interval=3)
        with patch.object(reporter, "event") as event:
            for seq in range(1, 4):
                reporter.telemetry_processed(
                    vehicle_id="usv-01",
                    session_id="session-one",
                    seq_id=seq,
                    state="NORMAL",
                    delivery_type="LIVE",
                    inserted=True,
                    duplicate=False,
                    ack_published=True,
                )
        categories = [call.args[0] for call in event.call_args_list]
        self.assertIn("TELEMETRY", categories)
