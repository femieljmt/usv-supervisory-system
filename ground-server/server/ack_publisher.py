"""Application-level ACK publisher for the USV backend."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from paho.mqtt import client as mqtt

from server.database import StoreResult
from server.protocol import (
    ACKStatus,
    PROTOCOL_VERSION,
    ack_topic,
    validate_ack_payload,
)


LOGGER = logging.getLogger(__name__)


def build_ack_payload(
    store_result: StoreResult,
) -> dict[str, Any]:
    """Build an ACK for a record already committed to SQLite."""

    payload: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "vehicle_id": store_result.vehicle_id,
        "session_id": store_result.session_id,
        "seq_id": store_result.seq_id,
        "status": ACKStatus.ACKED.value,
        "stored_at": store_result.stored_at,
    }

    validate_ack_payload(payload)
    return payload


class AckPublisher:
    """Publish ACK only after the database transaction succeeds."""

    def __init__(
        self,
        mqtt_client: mqtt.Client,
        *,
        qos: int = 1,
        publish_timeout: float = 5.0,
    ) -> None:
        if qos not in {0, 1, 2}:
            raise ValueError("qos harus bernilai 0, 1, atau 2")

        if publish_timeout <= 0:
            raise ValueError(
                "publish_timeout harus lebih besar dari 0"
            )

        self._mqtt_client = mqtt_client
        self._qos = qos
        self._publish_timeout = publish_timeout

    def publish_ack(
        self,
        payload: Mapping[str, Any],
    ) -> bool:
        """Publish and confirm that an ACK left the Paho client."""

        validate_ack_payload(payload)

        vehicle_id = str(payload["vehicle_id"])
        topic = ack_topic(vehicle_id)

        encoded_payload = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        try:
            publish_info = self._mqtt_client.publish(
                topic,
                encoded_payload,
                qos=self._qos,
                retain=False,
            )
        except Exception:
            LOGGER.exception(
                "Gagal memulai publikasi ACK untuk %s/%s/%s",
                payload["vehicle_id"],
                payload["session_id"],
                payload["seq_id"],
            )
            return False

        if publish_info.rc != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.error(
                "Publikasi ACK ditolak client MQTT. rc=%s",
                publish_info.rc,
            )
            return False

        try:
            publish_info.wait_for_publish(
                timeout=self._publish_timeout
            )
        except Exception:
            LOGGER.exception(
                "Gagal menunggu konfirmasi publish ACK"
            )
            return False

        if not publish_info.is_published():
            LOGGER.error(
                "ACK belum terpublikasi dalam %.1f detik",
                self._publish_timeout,
            )
            return False

        LOGGER.info(
            "ACK dipublikasikan: %s/%s/%s",
            payload["vehicle_id"],
            payload["session_id"],
            payload["seq_id"],
        )

        return True
