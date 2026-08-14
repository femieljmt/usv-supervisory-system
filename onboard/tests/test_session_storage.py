"""Tests for automatic per-session storage layout."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from onboard.session_storage import SessionStorage


class SessionStorageTest(TestCase):

    def test_creates_expected_session_layout_and_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            outbox = Path(directory) / "database" / "outbox.sqlite3"

            storage = SessionStorage(
                root,
                "20260711T004446Z-59f465",
            )
            paths = storage.create(
                vehicle_id="usv-01",
                outbox_path=outbox,
                batch_size=10,
                mqtt_host="100.64.0.20",
                mqtt_port=1883,
                mavlink_connection="udpin:0.0.0.0:14551",
                initial_pending_outbox=4,
            )

            self.assertTrue(paths.directory.is_dir())
            self.assertEqual(paths.operation_file.name, "operation.csv")
            self.assertEqual(paths.runtime_file.name, "runtime.log")
            self.assertEqual(paths.mission_file.name, "mission_plan.json")
            self.assertEqual(paths.info_file.name, "session_info.json")
            self.assertTrue(paths.info_file.exists())

            manifest = storage.read()
            self.assertEqual(manifest["vehicle_id"], "usv-01")
            self.assertEqual(
                manifest["session_id"],
                "20260711T004446Z-59f465",
            )
            self.assertEqual(manifest["status"], "RUNNING")
            self.assertEqual(manifest["batch_size"], 10)
            self.assertEqual(manifest["initial_pending_outbox"], 4)
            self.assertEqual(
                manifest["files"]["operation_csv"],
                str(paths.operation_file),
            )
            self.assertEqual(
                manifest["files"]["persistent_outbox"],
                str(outbox.resolve()),
            )

    def test_finalize_updates_shutdown_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            storage = SessionStorage(
                Path(directory) / "sessions",
                "session-test",
            )
            storage.create(
                vehicle_id="usv-01",
                outbox_path=Path(directory) / "outbox.sqlite3",
                batch_size=10,
                mqtt_host="127.0.0.1",
                mqtt_port=1883,
                mavlink_connection="udpin:0.0.0.0:14551",
                initial_pending_outbox=0,
            )

            storage.finalize(
                final_pending_outbox=3,
                exit_reason="signal_2",
                status="INTERRUPTED",
            )

            manifest = storage.read()
            self.assertEqual(manifest["status"], "INTERRUPTED")
            self.assertEqual(manifest["exit_reason"], "signal_2")
            self.assertEqual(manifest["final_pending_outbox"], 3)
            self.assertIsNotNone(manifest["ended_at_utc"])
            self.assertIsNotNone(manifest["ended_at_local"])

    def test_rejects_unsafe_session_identifier(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                SessionStorage(
                    Path(directory),
                    "../unsafe-session",
                )
