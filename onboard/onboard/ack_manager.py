"""Application ACK processing for the Raspberry Pi USV."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from onboard.persistent_outbox import PersistentOutbox
from onboard.protocol import (
    validate_ack_payload,
    validate_record_identity,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AckProcessResult:
    """Result of processing one application ACK."""

    valid: bool
    matched: bool
    removed: bool
    reason: str

    vehicle_id: str | None = None
    session_id: str | None = None
    seq_id: int | None = None
    latency_ms: float | None = None


class AckManager:
    """Validate ACK messages and remove exactly matching outbox records."""

    def __init__(
        self,
        vehicle_id: str,
        outbox: PersistentOutbox,
    ) -> None:
        self._vehicle_id = vehicle_id.strip()
        self._outbox = outbox

        if not self._vehicle_id:
            raise ValueError("vehicle_id tidak boleh kosong")

        self._lock = threading.RLock()

        self._published_at: dict[
            tuple[str, str, int],
            float,
        ] = {}

    def register_publish(
        self,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
        *,
        published_at: float | None = None,
    ) -> None:
        """Register when a record was handed to MQTT."""

        validate_record_identity(
            vehicle_id,
            session_id,
            seq_id,
        )

        if vehicle_id != self._vehicle_id:
            raise ValueError(
                "vehicle_id publish tidak sesuai konfigurasi"
            )

        identity = (
            vehicle_id,
            session_id,
            seq_id,
        )

        timestamp = (
            time.monotonic()
            if published_at is None
            else float(published_at)
        )

        with self._lock:
            self._published_at[identity] = timestamp

    def cancel_tracking(
        self,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
    ) -> bool:
        """Stop tracking a publish that MQTT rejected."""

        validate_record_identity(
            vehicle_id,
            session_id,
            seq_id,
        )

        identity = (
            vehicle_id,
            session_id,
            seq_id,
        )

        with self._lock:
            return self._published_at.pop(
                identity,
                None,
            ) is not None

    def mark_timeout(
        self,
        vehicle_id: str,
        session_id: str,
        seq_id: int,
    ) -> bool:
        """Return an unacknowledged record to PENDING."""

        validate_record_identity(
            vehicle_id,
            session_id,
            seq_id,
        )

        identity = (
            vehicle_id,
            session_id,
            seq_id,
        )

        with self._lock:
            self._published_at.pop(identity, None)

        return self._outbox.mark_pending(
            vehicle_id,
            session_id,
            seq_id,
        )

    def pending_count(self) -> int:
        with self._lock:
            return len(self._published_at)

    def process_message(
        self,
        raw_payload: bytes | str | Mapping[str, Any],
    ) -> AckProcessResult:
        """Decode, validate, and apply one ACK message."""

        try:
            payload = self._decode_payload(raw_payload)

            validate_ack_payload(
                payload,
                expected_vehicle_id=self._vehicle_id,
            )

        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            LOGGER.warning(
                "ACK tidak valid dan diabaikan: %s",
                exc,
            )

            return AckProcessResult(
                valid=False,
                matched=False,
                removed=False,
                reason=str(exc),
            )

        vehicle_id = str(payload["vehicle_id"])
        session_id = str(payload["session_id"])
        seq_id = int(payload["seq_id"])

        identity = (
            vehicle_id,
            session_id,
            seq_id,
        )

        removed = self._outbox.acknowledge(
            vehicle_id,
            session_id,
            seq_id,
        )

        with self._lock:
            published_at = self._published_at.pop(
                identity,
                None,
            )

        latency_ms: float | None = None

        if published_at is not None:
            latency_ms = round(
                (time.monotonic() - published_at)
                * 1000.0,
                2,
            )

        if not removed:
            LOGGER.info(
                "ACK valid tetapi record tidak ada di outbox: "
                "%s/%s/%s",
                vehicle_id,
                session_id,
                seq_id,
            )

            return AckProcessResult(
                valid=True,
                matched=False,
                removed=False,
                reason="RECORD_NOT_IN_OUTBOX",
                vehicle_id=vehicle_id,
                session_id=session_id,
                seq_id=seq_id,
                latency_ms=latency_ms,
            )

        LOGGER.info(
            "ACK valid; record dihapus dari outbox: "
            "%s/%s/%s",
            vehicle_id,
            session_id,
            seq_id,
        )

        return AckProcessResult(
            valid=True,
            matched=True,
            removed=True,
            reason="ACKED",
            vehicle_id=vehicle_id,
            session_id=session_id,
            seq_id=seq_id,
            latency_ms=latency_ms,
        )

    def _decode_payload(
        self,
        raw_payload: bytes | str | Mapping[str, Any],
    ) -> dict[str, Any]:
        if isinstance(raw_payload, Mapping):
            return dict(raw_payload)

        if isinstance(raw_payload, bytes):
            text = raw_payload.decode("utf-8")
        elif isinstance(raw_payload, str):
            text = raw_payload
        else:
            raise TypeError(
                "Payload ACK harus berupa bytes, string, atau mapping"
            )

        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Payload ACK bukan JSON valid"
            ) from exc

        if not isinstance(decoded, dict):
            raise ValueError(
                "Payload ACK harus berupa objek JSON"
            )

        return decoded
