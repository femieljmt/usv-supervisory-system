"""Non-blocking MQTT communication manager for Raspberry Pi USV."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any

from paho.mqtt import client as mqtt

from onboard.ack_manager import AckManager
from onboard.config import Settings
from onboard.protocol import (
    MQTTConnectionStatus,
    ack_topic,
    telemetry_topic,
    waypoints_topic,
    validate_telemetry_payload,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishResult:
    """Result of handing one telemetry payload to Paho MQTT."""

    accepted: bool
    topic: str
    rc: int
    message_id: int | None


class MQTTManager:
    """Manage MQTT connection, telemetry publishing, and ACK reception."""

    def __init__(
        self,
        settings: Settings,
        ack_manager: AckManager,
        *,
        mqtt_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._ack_manager = ack_manager

        self._lock = threading.RLock()
        self._started = False

        self._connection_status = (
            MQTTConnectionStatus.DISCONNECTED.value
        )

        if mqtt_client is None:
            self._client = self._create_client()
        else:
            self._client = mqtt_client
            self._configure_client(self._client)

    @property
    def connection_status(self) -> str:
        with self._lock:
            return self._connection_status

    @property
    def is_connected(self) -> bool:
        return (
            self.connection_status
            == MQTTConnectionStatus.CONNECTED.value
        )

    def start(self) -> None:
        """Start asynchronous MQTT connection."""

        with self._lock:
            if self._started:
                return

            self._started = True
            self._connection_status = (
                MQTTConnectionStatus.CONNECTING.value
            )

        LOGGER.info(
            "Menghubungkan MQTT onboard ke %s:%s",
            self._settings.mqtt_host,
            self._settings.mqtt_port,
        )

        try:
            self._client.connect_async(
                self._settings.mqtt_host,
                self._settings.mqtt_port,
                keepalive=self._settings.mqtt_keepalive,
            )
            self._client.loop_start()

        except Exception:
            with self._lock:
                self._started = False
                self._connection_status = (
                    MQTTConnectionStatus.DISCONNECTED.value
                )

            LOGGER.exception(
                "Gagal memulai koneksi MQTT"
            )

    def stop(self) -> None:
        """Stop the MQTT network loop."""

        with self._lock:
            if not self._started:
                return

            self._started = False

        try:
            self._client.disconnect()
        except Exception:
            LOGGER.exception(
                "Kesalahan saat disconnect MQTT"
            )

        try:
            self._client.loop_stop()
        except Exception:
            LOGGER.exception(
                "Kesalahan saat menghentikan loop MQTT"
            )

        with self._lock:
            self._connection_status = (
                MQTTConnectionStatus.DISCONNECTED.value
            )

    def publish_telemetry(
        self,
        payload: dict[str, Any],
    ) -> PublishResult:
        """Publish telemetry without waiting synchronously for ACK."""

        validate_telemetry_payload(payload)

        vehicle_id = str(payload["vehicle_id"])
        session_id = str(payload["session_id"])
        seq_id = int(payload["seq_id"])

        if vehicle_id != self._settings.vehicle_id:
            raise ValueError(
                "vehicle_id payload tidak sesuai konfigurasi onboard"
            )

        topic = telemetry_topic(vehicle_id)

        if not self.is_connected:
            return PublishResult(
                accepted=False,
                topic=topic,
                rc=mqtt.MQTT_ERR_NO_CONN,
                message_id=None,
            )

        encoded_payload = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        self._ack_manager.register_publish(
            vehicle_id,
            session_id,
            seq_id,
        )

        try:
            publish_info = self._client.publish(
                topic,
                encoded_payload,
                qos=self._settings.mqtt_qos,
                retain=False,
            )

        except Exception:
            self._ack_manager.cancel_tracking(
                vehicle_id,
                session_id,
                seq_id,
            )

            LOGGER.exception(
                "Exception saat publish telemetry %s/%s/%s",
                vehicle_id,
                session_id,
                seq_id,
            )

            return PublishResult(
                accepted=False,
                topic=topic,
                rc=mqtt.MQTT_ERR_UNKNOWN,
                message_id=None,
            )

        if publish_info.rc != mqtt.MQTT_ERR_SUCCESS:
            self._ack_manager.cancel_tracking(
                vehicle_id,
                session_id,
                seq_id,
            )

            LOGGER.warning(
                "Publish MQTT ditolak. seq_id=%s rc=%s",
                seq_id,
                publish_info.rc,
            )

            return PublishResult(
                accepted=False,
                topic=topic,
                rc=int(publish_info.rc),
                message_id=getattr(
                    publish_info,
                    "mid",
                    None,
                ),
            )

        LOGGER.info(
            "Telemetry diserahkan ke MQTT: "
            "%s/%s/%s mid=%s",
            vehicle_id,
            session_id,
            seq_id,
            getattr(publish_info, "mid", None),
        )

        return PublishResult(
            accepted=True,
            topic=topic,
            rc=int(publish_info.rc),
            message_id=getattr(
                publish_info,
                "mid",
                None,
            ),
        )


    def publish_waypoints(
        self,
        payload: dict[str, Any],
    ) -> PublishResult:
        """Publish a complete mission plan using retained MQTT delivery.

        Mission plan tidak menggunakan application ACK telemetry.
        Pesan retained membuat subscriber baru dapat menerima mission
        plan terakhir setelah tersambung ke broker.
        """

        vehicle_id = str(
            payload.get("vehicle_id", "")
        )

        if vehicle_id != self._settings.vehicle_id:
            raise ValueError(
                "vehicle_id mission plan tidak sesuai konfigurasi"
            )

        if payload.get("event_type") != "MISSION_PLAN":
            raise ValueError(
                "event_type mission plan tidak valid"
            )

        if not isinstance(
            payload.get("waypoints"),
            list,
        ):
            raise ValueError(
                "waypoints mission plan harus berupa list"
            )

        topic = waypoints_topic(vehicle_id)

        if not self.is_connected:
            return PublishResult(
                accepted=False,
                topic=topic,
                rc=mqtt.MQTT_ERR_NO_CONN,
                message_id=None,
            )

        encoded_payload = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        try:
            publish_info = self._client.publish(
                topic,
                encoded_payload,
                qos=self._settings.mqtt_qos,
                retain=True,
            )

        except Exception:
            LOGGER.exception(
                "Gagal publish mission plan"
            )

            return PublishResult(
                accepted=False,
                topic=topic,
                rc=mqtt.MQTT_ERR_UNKNOWN,
                message_id=None,
            )

        accepted = (
            publish_info.rc
            == mqtt.MQTT_ERR_SUCCESS
        )

        if accepted:
            LOGGER.info(
                "Mission plan diserahkan ke MQTT: "
                "topic=%s total=%s mid=%s",
                topic,
                len(payload["waypoints"]),
                getattr(
                    publish_info,
                    "mid",
                    None,
                ),
            )

        else:
            LOGGER.warning(
                "Publish mission plan ditolak: "
                "topic=%s rc=%s",
                topic,
                publish_info.rc,
            )

        return PublishResult(
            accepted=accepted,
            topic=topic,
            rc=int(publish_info.rc),
            message_id=getattr(
                publish_info,
                "mid",
                None,
            ),
        )

    def _create_client(self) -> mqtt.Client:
        client = mqtt.Client(
            callback_api_version=(
                mqtt.CallbackAPIVersion.VERSION2
            ),
            client_id=(
                f"{self._settings.vehicle_id}-supervisory"
            ),
            protocol=mqtt.MQTTv311,
        )

        self._configure_client(client)
        return client

    def _configure_client(self, client: Any) -> None:
        if self._settings.mqtt_username is not None:
            client.username_pw_set(
                self._settings.mqtt_username,
                self._settings.mqtt_password,
            )

        client.reconnect_delay_set(
            min_delay=1,
            max_delay=30,
        )

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        if hasattr(client, "on_connect_fail"):
            client.on_connect_fail = self._on_connect_fail


    def _on_connect_fail(
        self,
        client: Any,
        userdata: Any,
    ) -> None:
        with self._lock:
            self._connection_status = (
                MQTTConnectionStatus.DISCONNECTED.value
            )

        LOGGER.warning(
            "Koneksi MQTT belum berhasil; menunggu reconnect"
        )

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        connect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if self._reason_code_failed(reason_code):
            with self._lock:
                self._connection_status = (
                    MQTTConnectionStatus.DISCONNECTED.value
                )

            LOGGER.error(
                "Koneksi MQTT gagal: %s",
                reason_code,
            )
            return

        topic = ack_topic(
            self._settings.vehicle_id
        )

        result, message_id = client.subscribe(
            topic,
            qos=self._settings.mqtt_qos,
        )

        if result != mqtt.MQTT_ERR_SUCCESS:
            with self._lock:
                self._connection_status = (
                    MQTTConnectionStatus.DISCONNECTED.value
                )

            LOGGER.error(
                "Gagal subscribe topic ACK %s. rc=%s",
                topic,
                result,
            )
            return

        with self._lock:
            self._connection_status = (
                MQTTConnectionStatus.CONNECTED.value
            )

        LOGGER.info(
            "MQTT terhubung; subscribe ACK %s; mid=%s",
            topic,
            message_id,
        )

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        with self._lock:
            self._connection_status = (
                MQTTConnectionStatus.DISCONNECTED.value
            )

        if self._started:
            LOGGER.warning(
                "MQTT terputus: %s",
                reason_code,
            )
        else:
            LOGGER.info(
                "MQTT disconnect normal"
            )

    def _on_message(
        self,
        client: Any,
        userdata: Any,
        message: Any,
    ) -> None:
        expected_topic = ack_topic(
            self._settings.vehicle_id
        )

        if message.topic != expected_topic:
            LOGGER.warning(
                "Pesan MQTT dari topic yang tidak diharapkan: %s",
                message.topic,
            )
            return

        result = self._ack_manager.process_message(
            bytes(message.payload)
        )

        if not result.valid:
            LOGGER.warning(
                "ACK MQTT diabaikan: %s",
                result.reason,
            )
            return

        LOGGER.info(
            "ACK MQTT diproses: matched=%s removed=%s "
            "seq_id=%s latency_ms=%s",
            result.matched,
            result.removed,
            result.seq_id,
            result.latency_ms,
        )

    @staticmethod
    def _reason_code_failed(
        reason_code: Any,
    ) -> bool:
        if hasattr(reason_code, "is_failure"):
            return bool(reason_code.is_failure)

        try:
            return int(reason_code) != 0
        except (TypeError, ValueError):
            return True
