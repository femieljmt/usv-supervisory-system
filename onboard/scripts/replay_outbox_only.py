#!/usr/bin/env python3
"""Drain persistent onboard outbox without Pixhawk or normal onboard.app.

This utility:
- does not start MAVLink;
- does not create a new session;
- does not create new seq_id values;
- only publishes records already stored in the persistent outbox;
- waits for exact application ACK before records are removed.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

from onboard.ack_manager import AckManager
from onboard.config import load_settings
from onboard.mqtt_manager import MQTTManager
from onboard.persistent_outbox import PersistentOutbox
from onboard.synchronizer import OutboxSynchronizer


LOGGER = logging.getLogger("replay_outbox_only")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Kirim seluruh persistent outbox ke server tanpa Pixhawk "
            "dan tanpa menjalankan onboard.app."
        )
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Session yang ingin dipantau pada terminal.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=3600.0,
        help="Batas waktu keseluruhan proses. Default 3600 detik.",
    )
    parser.add_argument(
        "--connect-timeout-seconds",
        type=float,
        default=30.0,
        help="Batas waktu menunggu koneksi MQTT. Default 30 detik.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=2.0,
        help="Interval tampilan jumlah pending. Default 2 detik.",
    )
    return parser.parse_args()


def count_session_pending(db_path: Path, session_id: str | None) -> int | None:
    if not session_id:
        return None

    connection = sqlite3.connect(
        f"file:{db_path.resolve()}?mode=ro",
        uri=True,
        timeout=10.0,
    )
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM outbox
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        return int(row[0])
    finally:
        connection.close()


def main() -> int:
    args = parse_args()

    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds harus lebih besar dari 0")
    if args.connect_timeout_seconds <= 0:
        raise SystemExit("--connect-timeout-seconds harus lebih besar dari 0")
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds harus lebih besar dari 0")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = load_settings()
    outbox = PersistentOutbox(settings.outbox_db_path)
    outbox.initialize()
    recovered = outbox.reset_in_flight_to_pending()

    total_start = outbox.count()
    target_start = count_session_pending(
        settings.outbox_db_path,
        args.session_id,
    )

    print("=" * 72)
    print("REPLAY-ONLY PERSISTENT OUTBOX")
    print("=" * 72)
    print("Vehicle ID       :", settings.vehicle_id)
    print("MQTT broker      :", f"{settings.mqtt_host}:{settings.mqtt_port}")
    print("Outbox database  :", settings.outbox_db_path)
    print("Pending total    :", total_start)
    if args.session_id:
        print("Target session   :", args.session_id)
        print("Pending session  :", target_start)
    print("Recovered inflight:", recovered)
    print("Pixhawk/MAVProxy : TIDAK DIPERLUKAN")
    print("New session/seq  : TIDAK DIBUAT")
    print("=" * 72)

    if total_start == 0:
        print("Tidak ada record pending di outbox.")
        return 0

    stop_event = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        print(f"\nSignal {signum} diterima; menghentikan replay dengan aman...")
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    ack_manager = AckManager(settings.vehicle_id, outbox)
    mqtt_manager = MQTTManager(settings, ack_manager)
    synchronizer = OutboxSynchronizer(
        settings,
        outbox,
        mqtt_manager,
        ack_manager,
        delivery_enabled_provider=lambda: True,
    )

    started_at = time.monotonic()

    try:
        mqtt_manager.start()

        connect_deadline = (
            time.monotonic() + args.connect_timeout_seconds
        )
        while not stop_event.is_set() and not mqtt_manager.is_connected:
            if time.monotonic() >= connect_deadline:
                print(
                    "GAGAL: MQTT belum terhubung. Periksa broker, jaringan, "
                    "alamat MQTT_HOST, dan config/.env."
                )
                return 2
            time.sleep(0.2)

        if stop_event.is_set():
            return 130

        print("MQTT terhubung. Memulai pengiriman persistent outbox...")
        synchronizer.start()
        synchronizer.notify_new_record()

        last_total = None
        last_target = None

        while not stop_event.is_set():
            elapsed = time.monotonic() - started_at
            total_pending = outbox.count()
            target_pending = count_session_pending(
                settings.outbox_db_path,
                args.session_id,
            )
            in_flight = synchronizer.in_flight_count
            action = synchronizer.last_action

            if (
                total_pending != last_total
                or target_pending != last_target
            ):
                line = (
                    f"{time.strftime('%H:%M:%S')} | "
                    f"pending_total={total_pending} | "
                    f"in_flight={in_flight} | "
                    f"action={action}"
                )
                if args.session_id:
                    line += f" | pending_session={target_pending}"
                print(line, flush=True)
                last_total = total_pending
                last_target = target_pending

            if total_pending == 0 and in_flight == 0:
                print("=" * 72)
                print(
                    f"SELESAI: seluruh record telah menerima exact ACK "
                    f"dalam {elapsed:.1f} detik."
                )
                if args.session_id:
                    print(f"Pending session {args.session_id}: 0")
                print("=" * 72)
                return 0

            if elapsed >= args.timeout_seconds:
                print("=" * 72)
                print(
                    f"TIMEOUT: masih ada {total_pending} record. "
                    "Data tetap aman di persistent outbox."
                )
                if args.session_id:
                    print(
                        f"Pending session {args.session_id}: "
                        f"{target_pending}"
                    )
                print(
                    "Periksa server.app, ACK backend, MQTT, dan log server."
                )
                print("=" * 72)
                return 3

            synchronizer.notify_new_record()
            time.sleep(args.poll_seconds)

        print(
            f"Dihentikan operator. Pending total masih {outbox.count()} "
            "dan tetap aman di outbox."
        )
        return 130

    finally:
        try:
            synchronizer.stop()
        except Exception:
            LOGGER.exception("Gagal menghentikan synchronizer")
        try:
            mqtt_manager.stop()
        except Exception:
            LOGGER.exception("Gagal menghentikan MQTT manager")


if __name__ == "__main__":
    sys.exit(main())
