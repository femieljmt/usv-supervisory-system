"""Internet availability monitoring for Raspberry Pi USV.

Monitor ini memeriksa ketersediaan internet onboard melalui beberapa
endpoint publik. Status MQTT, broker, backend, Raspberry Pi Lab, dan ACK
tidak digunakan sebagai penentu langsung internet_status.
"""

from __future__ import annotations

import logging
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from onboard.config import Settings
from onboard.protocol import InternetStatus


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProbeTarget:
    host: str
    port: int

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class InternetSnapshot:
    status: InternetStatus
    consecutive_successes: int
    consecutive_failures: int
    checked_at: str | None
    successful_target: str | None


ProbeFunction = Callable[[ProbeTarget, float], bool]


class InternetMonitor:
    """Monitor internet availability with confirmation thresholds."""

    def __init__(
        self,
        settings: Settings,
        *,
        probe_function: ProbeFunction | None = None,
    ) -> None:
        self._interval = (
            settings.internet_probe_interval_seconds
        )
        self._timeout = (
            settings.internet_probe_timeout_seconds
        )
        self._success_confirmations = (
            settings.internet_success_confirmations
        )
        self._failure_confirmations = (
            settings.internet_failure_confirmations
        )

        self._targets = tuple(
            parse_probe_target(raw_target)
            for raw_target
            in settings.internet_probe_targets
        )

        self._probe_function = (
            probe_function or tcp_probe
        )

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._snapshot = InternetSnapshot(
            status=InternetStatus.UNKNOWN,
            consecutive_successes=0,
            consecutive_failures=0,
            checked_at=None,
            successful_target=None,
        )

    @property
    def running(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
        )

    @property
    def status(self) -> InternetStatus:
        with self._lock:
            return self._snapshot.status

    @property
    def available(self) -> bool:
        return self.status == InternetStatus.AVAILABLE

    def get_snapshot(self) -> InternetSnapshot:
        with self._lock:
            return self._snapshot

    def start(self) -> None:
        if self.running:
            return

        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run,
            name="internet-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def check_once(self) -> InternetSnapshot:
        """Run one complete internet probe cycle."""

        successful_target: str | None = None

        for target in self._targets:
            try:
                success = self._probe_function(
                    target,
                    self._timeout,
                )
            except Exception:
                LOGGER.exception(
                    "Probe internet gagal dijalankan: %s",
                    target.label,
                )
                success = False

            if success:
                successful_target = target.label
                break

        return self._apply_probe_result(
            success=successful_target is not None,
            successful_target=successful_target,
        )

    def _run(self) -> None:
        LOGGER.info(
            "Internet monitor dimulai; targets=%s",
            ", ".join(
                target.label
                for target in self._targets
            ),
        )

        while not self._stop_event.is_set():
            self.check_once()

            if self._stop_event.wait(self._interval):
                break

        LOGGER.info("Internet monitor dihentikan")

    def _apply_probe_result(
        self,
        *,
        success: bool,
        successful_target: str | None,
    ) -> InternetSnapshot:
        with self._lock:
            previous_status = self._snapshot.status

            if success:
                successes = (
                    self._snapshot.consecutive_successes
                    + 1
                )
                failures = 0

                if (
                    successes
                    >= self._success_confirmations
                ):
                    new_status = (
                        InternetStatus.AVAILABLE
                    )
                else:
                    new_status = previous_status

            else:
                successes = 0
                failures = (
                    self._snapshot.consecutive_failures
                    + 1
                )

                if (
                    failures
                    >= self._failure_confirmations
                ):
                    new_status = (
                        InternetStatus.UNAVAILABLE
                    )
                else:
                    new_status = previous_status

            self._snapshot = InternetSnapshot(
                status=new_status,
                consecutive_successes=successes,
                consecutive_failures=failures,
                checked_at=utcnow_iso(),
                successful_target=successful_target,
            )

            snapshot = self._snapshot

        if snapshot.status != previous_status:
            LOGGER.info(
                "Internet status berubah: %s -> %s",
                previous_status.value,
                snapshot.status.value,
            )

        return snapshot


def parse_probe_target(raw_target: str) -> ProbeTarget:
    """Parse target in host:port format."""

    if not isinstance(raw_target, str):
        raise ValueError(
            "Target probe harus berupa string"
        )

    cleaned = raw_target.strip()

    if ":" not in cleaned:
        raise ValueError(
            "Target probe harus menggunakan format host:port"
        )

    host, raw_port = cleaned.rsplit(":", 1)

    host = host.strip()
    raw_port = raw_port.strip()

    if not host:
        raise ValueError(
            "Host target probe tidak boleh kosong"
        )

    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError(
            f"Port target probe tidak valid: {raw_port!r}"
        ) from exc

    if not 1 <= port <= 65535:
        raise ValueError(
            "Port target probe harus berada pada rentang 1-65535"
        )

    return ProbeTarget(
        host=host,
        port=port,
    )


def tcp_probe(
    target: ProbeTarget,
    timeout: float,
) -> bool:
    """Return True when a TCP connection can be established."""

    try:
        with socket.create_connection(
            (target.host, target.port),
            timeout=timeout,
        ):
            return True
    except OSError:
        return False


def utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
