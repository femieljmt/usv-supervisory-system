"""Integrated MQTT telemetry and mission backend for Raspberry Pi Lab."""

from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from paho.mqtt import client as mqtt

from server.ack_publisher import AckPublisher, build_ack_payload
from server.config import Settings
from server.database import StoreResult, TelemetryDatabase
from server.mission_database import MissionDatabase, MissionStoreResult
from server.protocol import (
    telemetry_subscription_topic,
    validate_telemetry_payload,
    waypoints_subscription_topic,
)
from server.terminal_logging import ServerTerminalReporter


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomingMessage:
    topic: str
    payload: bytes
    retained: bool = False


@dataclass(frozen=True)
class MessageProcessResult:
    store_result: StoreResult
    ack_published: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class MissionProcessResult:
    store_result: MissionStoreResult
    payload: dict[str, Any]


class AckPublisherLike(Protocol):
    def publish_ack(self, payload: Mapping[str, Any]) -> bool:
        ...


class TelemetryMessageProcessor:
    """Validate, store, and ACK one telemetry message."""

    def __init__(
        self,
        database: TelemetryDatabase,
        ack_publisher: AckPublisherLike,
    ) -> None:
        self._database = database
        self._ack_publisher = ack_publisher

    def process(self, topic: str, raw_payload: bytes) -> MessageProcessResult:
        topic_vehicle_id = extract_vehicle_id(topic, expected_suffix="telemetry")
        payload = decode_json_object(raw_payload, label="telemetry")
        validate_telemetry_payload(payload)

        if payload["vehicle_id"] != topic_vehicle_id:
            raise ValueError("vehicle_id payload tidak sesuai dengan topic MQTT")

        store_result = self._database.store_telemetry(payload, source_topic=topic)
        ack_payload = build_ack_payload(store_result)
        ack_published = self._ack_publisher.publish_ack(ack_payload)

        if ack_published:
            self._database.mark_ack_sent(
                store_result.vehicle_id,
                store_result.session_id,
                store_result.seq_id,
            )

        return MessageProcessResult(
            store_result=store_result,
            ack_published=ack_published,
            payload=payload,
        )


class MissionMessageProcessor:
    """Validate and store a retained mission-plan message."""

    def __init__(self, database: MissionDatabase) -> None:
        self._database = database

    def process(self, topic: str, raw_payload: bytes) -> MissionProcessResult:
        topic_vehicle_id = extract_vehicle_id(topic, expected_suffix="waypoints")
        payload = decode_json_object(raw_payload, label="mission")
        if str(payload.get("vehicle_id", "")) != topic_vehicle_id:
            raise ValueError("vehicle_id mission tidak sesuai dengan topic MQTT")
        store_result = self._database.store(payload)
        return MissionProcessResult(store_result=store_result, payload=payload)


class MQTTBackend:
    """One MQTT client for telemetry, mission plan, database, and ACK."""

    def __init__(
        self,
        settings: Settings,
        database: TelemetryDatabase,
        mission_database: MissionDatabase | None = None,
        reporter: ServerTerminalReporter | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._mission_database = mission_database or MissionDatabase(
            settings.database_path
        )
        self._reporter = reporter or ServerTerminalReporter(
            summary_interval=settings.terminal_summary_interval
        )

        self._message_queue: queue.Queue[IncomingMessage | None] = queue.Queue()
        self._stopping = threading.Event()
        self._connected = threading.Event()
        self._mqtt_client = self._create_mqtt_client()
        self._ack_publisher = AckPublisher(
            self._mqtt_client,
            qos=settings.mqtt_qos,
            publish_timeout=5.0,
        )
        self._telemetry_processor = TelemetryMessageProcessor(
            database,
            self._ack_publisher,
        )
        self._mission_processor = MissionMessageProcessor(self._mission_database)
        self._worker: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        self._database.initialize()
        self._mission_database.initialize()
        self._stopping.clear()

        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="mqtt-storage-worker",
                daemon=True,
            )
            self._worker.start()

        LOGGER.info(
            "Menghubungkan backend ke MQTT broker %s:%s",
            self._settings.mqtt_host,
            self._settings.mqtt_port,
        )
        self._mqtt_client.connect_async(
            self._settings.mqtt_host,
            self._settings.mqtt_port,
            keepalive=self._settings.mqtt_keepalive,
        )
        self._mqtt_client.loop_start()

    def stop(self) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        self._connected.clear()

        try:
            self._mqtt_client.disconnect()
        except Exception:
            LOGGER.exception("Kesalahan saat disconnect MQTT")
        try:
            self._mqtt_client.loop_stop()
        except Exception:
            LOGGER.exception("Kesalahan saat menghentikan MQTT loop")

        self._message_queue.put(None)
        if self._worker is not None:
            self._worker.join(timeout=10.0)
        LOGGER.info("MQTT backend dihentikan")

    def wait_connected(self, timeout: float = 10.0) -> bool:
        return self._connected.wait(timeout)

    def _create_mqtt_client(self) -> mqtt.Client:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="usv-lab-server",
            protocol=mqtt.MQTTv311,
        )
        if self._settings.mqtt_username is not None:
            client.username_pw_set(
                self._settings.mqtt_username,
                self._settings.mqtt_password,
            )
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.enable_logger(logging.getLogger("paho.mqtt.server"))
        return client

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        connect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if getattr(reason_code, "is_failure", False):
            self._connected.clear()
            self._reporter.mqtt_status(False, str(reason_code))
            LOGGER.error("MQTT connect gagal: %s", reason_code)
            return

        topics = [
            (telemetry_subscription_topic(), self._settings.mqtt_qos),
            (waypoints_subscription_topic(), self._settings.mqtt_qos),
        ]
        result, message_id = client.subscribe(topics)
        if result != mqtt.MQTT_ERR_SUCCESS:
            self._connected.clear()
            self._reporter.mqtt_status(False, f"subscribe rc={result}")
            LOGGER.error("Gagal subscribe topic server. rc=%s", result)
            return

        self._connected.set()
        self._reporter.mqtt_status(True, "telemetry + waypoints")
        LOGGER.info("MQTT terhubung; subscriptions=%s mid=%s", topics, message_id)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        self._connected.clear()
        if self._stopping.is_set():
            LOGGER.info("MQTT disconnect normal")
            return
        self._reporter.mqtt_status(False, str(reason_code))
        LOGGER.warning("MQTT terputus: %s", reason_code)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        if self._stopping.is_set():
            return
        self._message_queue.put(
            IncomingMessage(
                topic=message.topic,
                payload=bytes(message.payload),
                retained=bool(message.retain),
            )
        )

    def _worker_loop(self) -> None:
        while True:
            incoming = self._message_queue.get()
            try:
                if incoming is None:
                    return

                if incoming.topic.endswith("/telemetry"):
                    result = self._telemetry_processor.process(
                        incoming.topic,
                        incoming.payload,
                    )
                    store = result.store_result
                    payload = result.payload
                    LOGGER.info(
                        "Telemetry diproses: vehicle=%s session=%s seq=%s "
                        "inserted=%s duplicate=%s ack=%s",
                        store.vehicle_id,
                        store.session_id,
                        store.seq_id,
                        store.inserted,
                        store.duplicate,
                        result.ack_published,
                    )
                    self._reporter.telemetry_processed(
                        vehicle_id=store.vehicle_id,
                        session_id=store.session_id,
                        seq_id=store.seq_id,
                        state=str(payload["supervisor_state"]),
                        delivery_type=str(payload["delivery_type"]),
                        inserted=store.inserted,
                        duplicate=store.duplicate,
                        ack_published=result.ack_published,
                    )

                elif incoming.topic.endswith("/waypoints"):
                    result = self._mission_processor.process(
                        incoming.topic,
                        incoming.payload,
                    )
                    payload = result.payload
                    store = result.store_result
                    LOGGER.info(
                        "Mission diproses: vehicle=%s session=%s total=%s "
                        "inserted=%s retained=%s",
                        payload["vehicle_id"],
                        payload["session_id"],
                        payload["mission_total"],
                        store.inserted,
                        incoming.retained,
                    )
                    self._reporter.mission_processed(
                        vehicle_id=str(payload["vehicle_id"]),
                        session_id=str(payload["session_id"]),
                        mission_total=int(payload["mission_total"]),
                        executable_total=int(payload["executable_total"]),
                        inserted=store.inserted,
                        retained=incoming.retained,
                    )
                else:
                    LOGGER.warning("Topic tidak dikenali: %s", incoming.topic)

            except Exception:
                LOGGER.exception("Pesan MQTT gagal diproses; ACK tidak dikirim")
                self._reporter.event(
                    "ERROR",
                    f"Message rejected topic={incoming.topic if incoming else '-'}",
                    level="ERROR",
                )
            finally:
                self._message_queue.task_done()


def decode_json_object(raw_payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        decoded = raw_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Payload {label} bukan UTF-8 valid") from exc
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Payload {label} bukan JSON valid") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Payload {label} harus berupa objek JSON")
    return payload


def extract_vehicle_id(topic: str, expected_suffix: str = "telemetry") -> str:
    """Extract vehicle_id from usv/{vehicle_id}/{expected_suffix}."""

    if not isinstance(topic, str):
        raise ValueError("Topic MQTT harus berupa string")
    parts = topic.split("/")
    if (
        len(parts) != 3
        or parts[0] != "usv"
        or parts[2] != expected_suffix
        or not parts[1]
    ):
        raise ValueError(f"Topic {expected_suffix} tidak valid: {topic!r}")
    vehicle_id = parts[1]
    if any(character in vehicle_id for character in "/+#"):
        raise ValueError("vehicle_id pada topic MQTT tidak valid")
    return vehicle_id
