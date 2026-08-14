"""Operation record builder for the Raspberry Pi USV.

Modul ini menggabungkan identitas record, snapshot MAVLink, supervisor
state, dan status komunikasi menjadi payload sesuai protocol_contract.md.

Modul ini tidak:
- mengirim MQTT;
- menulis persistent outbox;
- menghapus buffer;
- menerima ACK;
- membuat nilai telemetry yang tidak diterima dari Pixhawk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from onboard.models import TelemetrySnapshot
from onboard.protocol import (
    ACKStatus,
    APLinkStatus,
    DATA_SOURCE_PIXHAWK,
    DeliveryType,
    InternetStatus,
    MQTTConnectionStatus,
    MQTTPublishStatus,
    PROTOCOL_VERSION,
    SupervisorState,
    SyncStatus,
    validate_telemetry_payload,
)
from onboard.sequence_manager import (
    RecordIdentity,
    SequenceManager,
)


MISSION_IDLE = "IDLE"
MISSION_RUNNING = "RUNNING"
MISSION_COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class RecordBuildContext:
    """Non-MAVLink values required to build one operation record."""

    supervisor_state: SupervisorState
    internet_status: InternetStatus
    mqtt_connection_status: MQTTConnectionStatus
    sync_status: SyncStatus

    pixhawk_available: bool

    # Nilai ini harus menggambarkan jumlah outbox setelah record baru
    # dimasukkan. Pada integrasi app.py nanti nilainya dihitung sebagai:
    # outbox.count() + 1
    buffer_count_after_enqueue: int


class OperationRecordBuilder:
    """Build validated LIVE telemetry records."""

    def __init__(
        self,
        sequence_manager: SequenceManager,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._sequence_manager = sequence_manager
        self._clock = clock or utcnow_iso

    def build_live_record(
        self,
        snapshot: TelemetrySnapshot,
        context: RecordBuildContext,
    ) -> dict:
        """Create one validated LIVE record.

        Calling this method allocates a new persistent seq_id.
        """

        self._validate_context(context)

        identity = self._sequence_manager.next_identity()

        payload = self._build_payload(
            identity=identity,
            snapshot=snapshot,
            context=context,
        )

        validate_telemetry_payload(payload)

        return payload

    def _build_payload(
        self,
        *,
        identity: RecordIdentity,
        snapshot: TelemetrySnapshot,
        context: RecordBuildContext,
    ) -> dict:
        ap_link = (
            APLinkStatus.OK
            if context.pixhawk_available
            else APLinkStatus.LOST
        )

        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "vehicle_id": identity.vehicle_id,
            "session_id": identity.session_id,
            "seq_id": identity.seq_id,
            "timestamp": self._clock(),
            "data_source": DATA_SOURCE_PIXHAWK,
            "delivery_type": DeliveryType.LIVE.value,
            "supervisor_state": (
                context.supervisor_state.value
            ),
            "mission_status": determine_mission_status(
                snapshot
            ),
            "ap_link": ap_link.value,
            "internet_status": (
                context.internet_status.value
            ),
            "mqtt_connection_status": (
                context.mqtt_connection_status.value
            ),
            "mqtt_publish_status": (
                MQTTPublishStatus.PENDING.value
            ),
            "ack_status": ACKStatus.PENDING.value,
            "ack_latency_ms": None,
            "retry_count": 0,
            "buffer_count": (
                context.buffer_count_after_enqueue
            ),
            "sync_status": context.sync_status.value,
        }

        payload.update(snapshot.to_payload_fields())

        return payload

    @staticmethod
    def _validate_context(
        context: RecordBuildContext,
    ) -> None:
        if not isinstance(
            context.supervisor_state,
            SupervisorState,
        ):
            raise ValueError(
                "supervisor_state harus berupa SupervisorState"
            )

        if not isinstance(
            context.internet_status,
            InternetStatus,
        ):
            raise ValueError(
                "internet_status harus berupa InternetStatus"
            )

        if not isinstance(
            context.mqtt_connection_status,
            MQTTConnectionStatus,
        ):
            raise ValueError(
                "mqtt_connection_status harus berupa "
                "MQTTConnectionStatus"
            )

        if not isinstance(
            context.sync_status,
            SyncStatus,
        ):
            raise ValueError(
                "sync_status harus berupa SyncStatus"
            )

        if not isinstance(
            context.pixhawk_available,
            bool,
        ):
            raise ValueError(
                "pixhawk_available harus berupa boolean"
            )

        buffer_count = context.buffer_count_after_enqueue

        if (
            isinstance(buffer_count, bool)
            or not isinstance(buffer_count, int)
        ):
            raise ValueError(
                "buffer_count_after_enqueue harus berupa integer"
            )

        if buffer_count < 0:
            raise ValueError(
                "buffer_count_after_enqueue tidak boleh negatif"
            )


def determine_mission_status(
    snapshot: TelemetrySnapshot,
) -> str:
    """Determine mission status from received MAVLink values."""

    if snapshot.mission_complete:
        return MISSION_COMPLETED

    if (
        snapshot.flight_mode == "AUTO"
        and snapshot.armed
    ):
        return MISSION_RUNNING

    return MISSION_IDLE


def utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
