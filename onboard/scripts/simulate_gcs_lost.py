#!/usr/bin/env python3
"""Controlled GCS link-loss fault injector for the USV onboard Raspberry Pi.

This utility does not modify the supervisory application. It temporarily
installs an isolated iptables OUTPUT chain so that:

1. Internet probe targets are unreachable, causing NET to become DOWN.
2. The MQTT/GCS server is unreachable, so telemetry cannot be delivered.
3. Loopback MAVLink (127.0.0.1:14551) and local logging remain unaffected.

The rules are removed automatically after the requested duration, on Ctrl+C,
or by a detached watchdog if the main process is interrupted unexpectedly.

Run as root, for example:

    sudo python3 scripts/simulate_gcs_lost.py --delay 10 --gcs-seconds 30

The default "controlled" mode is repeatable and preserves unrelated network
traffic. The optional "full-interface" mode blocks all outbound traffic on the
selected interface and can interrupt SSH/Tailscale; use it only from a local
console or tmux session.
"""

from __future__ import annotations

import argparse
import atexit
import fcntl
import ipaddress
import json
import logging
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence


CHAIN_NAME = "USV_GCSLOSS"
LOCK_PATH = Path("/run/usv_gcs_loss_test.lock")
STATE_PATH = Path("/run/usv_gcs_loss_test.json")
WIB = timezone(timedelta(hours=7))
LOGGER = logging.getLogger("usv.gcs_loss_simulator")


@dataclass(frozen=True)
class ProbeTarget:
    host: str
    port: int


@dataclass(frozen=True)
class DropRule:
    address: str
    port: int | None = None
    label: str = ""


@dataclass
class TestRecord:
    test_id: str
    mode: str
    status: str
    requested_delay_seconds: float
    requested_block_seconds: float
    requested_gcs_seconds: float | None
    estimated_detection_seconds: float
    started_at_utc: str | None
    link_loss_started_at_utc: str | None
    link_loss_expected_end_utc: str | None
    link_loss_ended_at_utc: str | None
    finished_at_utc: str | None
    mqtt_host: str
    probe_targets: list[str]
    drop_addresses: list[str]
    interface: str | None
    evidence_file: str | None
    note: str


class CommandError(RuntimeError):
    pass


class CommandRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        LOGGER.debug("command: %s", " ".join(command))
        return subprocess.run(
            list(command),
            check=check,
            capture_output=capture_output,
            text=True,
        )


class FirewallController:
    """Install and remove an isolated iptables chain."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()
        self.iptables = shutil.which("iptables")
        if not self.iptables:
            raise CommandError(
                "Perintah iptables tidak ditemukan. Instal paket iptables terlebih dahulu."
            )

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.runner.run(
            [self.iptables, "-w", "5", *args],
            check=check,
        )

    def cleanup(self) -> None:
        """Remove only rules created by this test. Safe to call repeatedly."""
        while True:
            result = self._run("-C", "OUTPUT", "-j", CHAIN_NAME, check=False)
            if result.returncode != 0:
                break
            self._run("-D", "OUTPUT", "-j", CHAIN_NAME, check=False)

        self._run("-F", CHAIN_NAME, check=False)
        self._run("-X", CHAIN_NAME, check=False)

    def apply_controlled(self, rules: Iterable[DropRule]) -> None:
        self.cleanup()
        self._run("-N", CHAIN_NAME)

        try:
            for rule in rules:
                args = ["-A", CHAIN_NAME, "-d", f"{rule.address}/32"]
                if rule.port is not None:
                    args += ["-p", "tcp", "--dport", str(rule.port)]
                args += ["-j", "DROP"]
                self._run(*args)

            self._run("-I", "OUTPUT", "1", "-j", CHAIN_NAME)
        except Exception:
            self.cleanup()
            raise

    def apply_full_interface(self, interface: str) -> None:
        self.cleanup()
        self._run("-N", CHAIN_NAME)
        try:
            self._run(
                "-A",
                CHAIN_NAME,
                "-o",
                interface,
                "-j",
                "DROP",
            )
            self._run("-I", "OUTPUT", "1", "-j", CHAIN_NAME)
        except Exception:
            self.cleanup()
            raise


class FakeCommandRunner(CommandRunner):
    """Small test helper; not used during normal execution."""

    def __init__(self, returncodes: list[int] | None = None) -> None:
        self.commands: list[list[str]] = []
        self.returncodes = list(returncodes or [])

    def run(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        code = self.returncodes.pop(0) if self.returncodes else 0
        if check and code != 0:
            raise subprocess.CalledProcessError(code, command)
        return subprocess.CompletedProcess(command, code, stdout="", stderr="")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def wib_now_text() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"File konfigurasi tidak ditemukan: {path}")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_probe_target(raw: str) -> ProbeTarget:
    value = raw.strip()
    if not value or ":" not in value:
        raise ValueError(f"Target probe tidak valid: {raw!r}")
    host, port_text = value.rsplit(":", 1)
    port = int(port_text)
    if not host or not (1 <= port <= 65535):
        raise ValueError(f"Target probe tidak valid: {raw!r}")
    return ProbeTarget(host=host, port=port)


def resolve_ipv4(host: str) -> list[str]:
    try:
        address = ipaddress.ip_address(host)
        if address.version != 4:
            raise ValueError(f"Alamat IPv6 belum didukung: {host}")
        return [str(address)]
    except ValueError:
        pass

    addresses: set[str] = set()
    for result in socket.getaddrinfo(host, None, family=socket.AF_INET):
        addresses.add(result[4][0])
    if not addresses:
        raise ValueError(f"Tidak dapat menemukan alamat IPv4 untuk {host}")
    return sorted(addresses)


def build_controlled_rules(
    mqtt_host: str,
    probe_targets: Sequence[ProbeTarget],
) -> list[DropRule]:
    rules: list[DropRule] = []
    seen: set[tuple[str, int | None]] = set()

    for target in probe_targets:
        for address in resolve_ipv4(target.host):
            key = (address, target.port)
            if key not in seen:
                rules.append(
                    DropRule(
                        address=address,
                        port=target.port,
                        label=f"internet-probe {target.host}:{target.port}",
                    )
                )
                seen.add(key)

    for address in resolve_ipv4(mqtt_host):
        key = (address, None)
        if key not in seen:
            rules.append(
                DropRule(
                    address=address,
                    port=None,
                    label=f"GCS/MQTT {mqtt_host}",
                )
            )
            seen.add(key)

    return rules


def estimate_down_detection_seconds(
    target_count: int,
    probe_timeout: float,
    probe_interval: float,
    failure_confirmations: int,
) -> float:
    """Conservative estimate before NET changes to DOWN.

    Each failed cycle can spend up to timeout per target, followed by the
    configured probe interval. The estimate intentionally rounds up so a
    requested GCS_LOST duration is not shorter than expected.
    """
    cycle_seconds = max(1, target_count) * probe_timeout + probe_interval
    return float(math.ceil(cycle_seconds * failure_confirmations))


def default_interface(runner: CommandRunner | None = None) -> str:
    runner = runner or CommandRunner()
    ip_command = shutil.which("ip")
    if not ip_command:
        raise CommandError("Perintah ip tidak ditemukan")
    result = runner.run([ip_command, "route", "show", "default"])
    for line in result.stdout.splitlines():
        parts = line.split()
        if "dev" in parts:
            index = parts.index("dev")
            if index + 1 < len(parts):
                return parts[index + 1]
    raise CommandError("Interface default route tidak dapat ditentukan")


def latest_session_directory(project_root: Path) -> Path | None:
    sessions_root = project_root / "data" / "sessions"
    if not sessions_root.exists():
        return None
    candidates = [item for item in sessions_root.iterdir() if item.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def choose_evidence_path(project_root: Path, test_id: str) -> Path:
    session_dir = latest_session_directory(project_root)
    if session_dir is not None:
        return session_dir / f"gcs_loss_test_{test_id}.json"

    fallback = project_root / "data" / "link_loss_tests"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback / f"gcs_loss_test_{test_id}.json"


def write_record(path: Path, record: TestRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(record), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_state_file(payload: dict[str, object]) -> None:
    STATE_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_state_file() -> dict[str, object] | None:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def remove_state_file() -> None:
    try:
        STATE_PATH.unlink()
    except FileNotFoundError:
        pass


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError(
            "Program harus dijalankan dengan sudo karena memasang aturan firewall."
        )


def acquire_lock() -> object:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("Pengujian GCS_LOST lain sedang berjalan") from exc
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def configure_logging(project_root: Path, verbose: bool = False) -> Path:
    log_dir = project_root / "data" / "logs" / "link_loss_tests"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"gcs_loss_{datetime.now(WIB):%Y%m%d_%H%M%S}.log"

    LOGGER.setLevel(logging.DEBUG if verbose else logging.INFO)
    LOGGER.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    LOGGER.addHandler(console)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)
    return log_path


def sleep_countdown(seconds: float, label: str, stop_flag: list[bool]) -> bool:
    deadline = time.monotonic() + seconds
    last_printed: int | None = None
    while True:
        if stop_flag[0]:
            return False
        remaining = max(0.0, deadline - time.monotonic())
        rounded = int(math.ceil(remaining))
        if rounded != last_printed and (
            rounded <= 10 or rounded % 5 == 0 or last_printed is None
        ):
            LOGGER.info("%s: %d detik", label, rounded)
            last_printed = rounded
        if remaining <= 0:
            return True
        time.sleep(min(0.25, remaining))


def start_cleanup_watchdog(
    script_path: Path,
    delay_seconds: float,
    log_path: Path,
) -> subprocess.Popen[str]:
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            str(script_path),
            "--watchdog-cleanup-after",
            str(delay_seconds),
        ],
        stdout=log_handle,
        stderr=log_handle,
        text=True,
        start_new_session=True,
        close_fds=True,
    )
    log_handle.close()
    return process


def terminate_watchdog(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2.0)
    except Exception:
        pass


def cleanup_firewall() -> None:
    try:
        FirewallController().cleanup()
    except Exception as exc:
        print(f"Peringatan: cleanup firewall gagal: {exc}", file=sys.stderr)
    remove_state_file()


def watchdog_cleanup(after_seconds: float) -> int:
    require_root()
    time.sleep(max(0.0, after_seconds))
    cleanup_firewall()
    return 0


def show_status() -> int:
    state = read_state_file()
    if state is None:
        print("Tidak ada simulasi GCS_LOST aktif.")
        return 0
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulasi GCS_LOST terkontrol untuk Raspberry Pi USV",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=10.0,
        help="Jeda sebelum link-loss diaktifkan",
    )
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument(
        "--duration",
        type=float,
        help="Durasi firewall aktif (bukan durasi state GCS_LOST)",
    )
    duration.add_argument(
        "--gcs-seconds",
        type=float,
        help=(
            "Target perkiraan durasi state GCS_LOST. Program menambahkan "
            "estimasi waktu deteksi NET DOWN secara otomatis"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("controlled", "full-interface"),
        default="controlled",
        help="controlled memblokir probe+GCS; full-interface memblokir interface",
    )
    parser.add_argument(
        "--interface",
        default="auto",
        help="Interface untuk mode full-interface",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Konfirmasi risiko mode full-interface",
    )
    parser.add_argument(
        "--env",
        type=Path,
        help="Lokasi config/.env onboard",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        help="Root proyek onboard",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument(
        "--watchdog-cleanup-after",
        type=float,
        help=argparse.SUPPRESS,
    )
    return parser


def determine_project_root(args: argparse.Namespace) -> Path:
    if args.project_root:
        return args.project_root.expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def validate_positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} harus lebih besar dari 0")


def run_test(args: argparse.Namespace) -> int:
    require_root()
    project_root = determine_project_root(args)
    env_path = args.env or project_root / "config" / ".env"
    env = parse_env(env_path)

    mqtt_host = env.get("MQTT_HOST", "").strip()
    if not mqtt_host:
        raise ValueError("MQTT_HOST tidak ditemukan pada config/.env")

    raw_targets = env.get("INTERNET_PROBE_TARGETS", "").strip()
    if not raw_targets:
        raise ValueError("INTERNET_PROBE_TARGETS tidak ditemukan pada config/.env")
    probe_targets = [
        parse_probe_target(item)
        for item in raw_targets.split(",")
        if item.strip()
    ]

    probe_timeout = float(env.get("INTERNET_PROBE_TIMEOUT_SECONDS", "1.0"))
    probe_interval = float(env.get("INTERNET_PROBE_INTERVAL_SECONDS", "2.0"))
    failure_confirmations = int(env.get("INTERNET_FAILURE_CONFIRMATIONS", "3"))
    success_confirmations = int(env.get("INTERNET_SUCCESS_CONFIRMATIONS", "2"))

    if args.delay < 0:
        raise ValueError("--delay tidak boleh negatif")

    estimated_detection = estimate_down_detection_seconds(
        len(probe_targets),
        probe_timeout,
        probe_interval,
        failure_confirmations,
    )

    if args.gcs_seconds is not None:
        validate_positive("--gcs-seconds", args.gcs_seconds)
        block_seconds = estimated_detection + args.gcs_seconds
        requested_gcs_seconds = args.gcs_seconds
    else:
        block_seconds = args.duration if args.duration is not None else 30.0
        validate_positive("--duration", block_seconds)
        requested_gcs_seconds = None

    if args.mode == "full-interface" and not args.yes:
        raise ValueError(
            "Mode full-interface dapat memutus SSH/Tailscale. Tambahkan --yes "
            "dan jalankan dari console lokal atau tmux."
        )

    log_path = configure_logging(project_root, args.verbose)
    lock_handle = acquire_lock()

    controller = FirewallController()
    controller.cleanup()

    interface: str | None = None
    rules: list[DropRule] = []
    if args.mode == "controlled":
        rules = build_controlled_rules(mqtt_host, probe_targets)
    else:
        interface = (
            default_interface()
            if args.interface == "auto"
            else args.interface
        )

    test_id = f"{datetime.now(WIB):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    evidence_path = choose_evidence_path(project_root, test_id)
    record = TestRecord(
        test_id=test_id,
        mode=args.mode,
        status="PREPARING",
        requested_delay_seconds=args.delay,
        requested_block_seconds=block_seconds,
        requested_gcs_seconds=requested_gcs_seconds,
        estimated_detection_seconds=estimated_detection,
        started_at_utc=utcnow_iso(),
        link_loss_started_at_utc=None,
        link_loss_expected_end_utc=None,
        link_loss_ended_at_utc=None,
        finished_at_utc=None,
        mqtt_host=mqtt_host,
        probe_targets=[f"{item.host}:{item.port}" for item in probe_targets],
        drop_addresses=[
            f"{item.address}:{item.port}" if item.port else item.address
            for item in rules
        ],
        interface=interface,
        evidence_file=str(evidence_path),
        note=(
            "Software-controlled fault injection. Data ini bukan bukti putusnya "
            "modem fisik; gunakan sebagai pengujian link-loss terkontrol."
        ),
    )
    write_record(evidence_path, record)

    LOGGER.info("USV GCS_LOST CONTROLLED TEST")
    LOGGER.info("Test ID             : %s", test_id)
    LOGGER.info("Mode                : %s", args.mode)
    LOGGER.info("Mulai               : %s", wib_now_text())
    LOGGER.info("Delay               : %.1f detik", args.delay)
    LOGGER.info("Estimasi NET DOWN   : %.1f detik", estimated_detection)
    if requested_gcs_seconds is not None:
        LOGGER.info("Target GCS_LOST     : %.1f detik", requested_gcs_seconds)
    LOGGER.info("Firewall aktif      : %.1f detik", block_seconds)
    LOGGER.info("MQTT/GCS            : %s", mqtt_host)
    LOGGER.info("Evidence            : %s", evidence_path)
    LOGGER.info("Log                 : %s", log_path)

    if args.mode == "controlled":
        for rule in rules:
            suffix = f":{rule.port}" if rule.port else ""
            LOGGER.info("Drop target         : %s%s (%s)", rule.address, suffix, rule.label)
    else:
        LOGGER.warning("FULL INTERFACE      : %s", interface)
        LOGGER.warning("SSH/Tailscale kemungkinan terputus sampai timer selesai")

    if args.dry_run:
        LOGGER.info("DRY RUN: firewall tidak diubah")
        record.status = "DRY_RUN"
        record.finished_at_utc = utcnow_iso()
        write_record(evidence_path, record)
        return 0

    stop_flag = [False]
    active = [False]
    watchdog: list[subprocess.Popen[str] | None] = [None]

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.warning("Signal %s diterima; pemulihan dipercepat", signum)
        stop_flag[0] = True

    previous_handlers = {
        signal.SIGINT: signal.getsignal(signal.SIGINT),
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
    }
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    def final_cleanup() -> None:
        if active[0]:
            try:
                controller.cleanup()
            except Exception:
                LOGGER.exception("Cleanup firewall pada exit gagal")
            active[0] = False
        terminate_watchdog(watchdog[0])
        remove_state_file()

    atexit.register(final_cleanup)

    try:
        record.status = "COUNTDOWN"
        write_record(evidence_path, record)
        if args.delay > 0 and not sleep_countdown(args.delay, "Link-loss dimulai", stop_flag):
            record.status = "CANCELLED_BEFORE_START"
            record.finished_at_utc = utcnow_iso()
            write_record(evidence_path, record)
            LOGGER.info("Pengujian dibatalkan sebelum firewall aktif")
            return 130

        if args.mode == "controlled":
            controller.apply_controlled(rules)
        else:
            assert interface is not None
            controller.apply_full_interface(interface)
        active[0] = True

        started_at = datetime.now(timezone.utc)
        expected_end = started_at + timedelta(seconds=block_seconds)
        record.status = "LINK_LOSS_ACTIVE"
        record.link_loss_started_at_utc = started_at.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
        record.link_loss_expected_end_utc = expected_end.isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        write_record(evidence_path, record)

        write_state_file(
            {
                "test_id": test_id,
                "mode": args.mode,
                "pid": os.getpid(),
                "started_at_utc": record.link_loss_started_at_utc,
                "expected_end_utc": record.link_loss_expected_end_utc,
                "evidence_file": str(evidence_path),
            }
        )

        watchdog[0] = start_cleanup_watchdog(
            Path(__file__).resolve(),
            block_seconds + 15.0,
            log_path,
        )

        LOGGER.warning("LINK-LOSS AKTIF")
        LOGGER.warning("MAVLink loopback dan local logging tetap berjalan")
        LOGGER.warning("Tunggu NET=DOWN dan state NORMAL -> GCS_LOST")

        sleep_countdown(block_seconds, "Link-loss aktif, sisa", stop_flag)

        controller.cleanup()
        active[0] = False
        terminate_watchdog(watchdog[0])
        remove_state_file()

        record.status = "RESTORING"
        record.link_loss_ended_at_utc = utcnow_iso()
        write_record(evidence_path, record)

        LOGGER.info("LINK-LOSS DINONAKTIFKAN")
        LOGGER.info(
            "Perkiraan NET kembali UP setelah %d konfirmasi sukses (sekitar %.1f-%.1f detik)",
            success_confirmations,
            success_confirmations * probe_interval,
            success_confirmations * (probe_interval + probe_timeout),
        )
        LOGGER.info("Amati GCS_LOST -> RECOVERY -> NORMAL dan batch maksimum 10")

        record.status = "COMPLETED" if not stop_flag[0] else "STOPPED_EARLY"
        record.finished_at_utc = utcnow_iso()
        write_record(evidence_path, record)
        return 0 if not stop_flag[0] else 130
    finally:
        final_cleanup()
        try:
            lock_handle.close()
        except Exception:
            pass
        signal.signal(signal.SIGINT, previous_handlers[signal.SIGINT])
        signal.signal(signal.SIGTERM, previous_handlers[signal.SIGTERM])


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.watchdog_cleanup_after is not None:
            return watchdog_cleanup(args.watchdog_cleanup_after)
        if args.cleanup:
            require_root()
            cleanup_firewall()
            print("Aturan simulasi GCS_LOST sudah dibersihkan.")
            return 0
        if args.status:
            return show_status()
        return run_test(args)
    except KeyboardInterrupt:
        cleanup_firewall()
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
