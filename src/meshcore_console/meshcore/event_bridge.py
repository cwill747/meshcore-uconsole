from __future__ import annotations

from typing import Any

from meshcore_console.core.types import (
    EmitCallback,
    EventServiceProtocol,
    EventSubscriberProtocol,
    LoggerCallback,
    MeshNodeProtocol,
)

from .packet_codec import packet_to_dict


def attach_event_service_subscriber(
    *,
    event_service: EventServiceProtocol,
    event_subscriber_base: type[EventSubscriberProtocol],
    emit: EmitCallback,
    logger: LoggerCallback,
) -> EventSubscriberProtocol:
    class QueueSubscriber(event_subscriber_base):  # type: ignore[misc,valid-type]
        async def handle_event(self, event_type: str, data: dict[str, Any]) -> None:
            emit({"type": event_type, "data": data})

    subscriber = QueueSubscriber()
    event_service.subscribe_all(subscriber)
    logger("registered EventService global subscriber")
    return subscriber


def attach_dispatcher_callbacks(
    *,
    node: MeshNodeProtocol,
    emit: EmitCallback,
    logger: LoggerCallback,
) -> None:
    dispatcher = node.dispatcher

    async def on_packet(packet: Any) -> None:
        data = packet_to_dict(packet)
        logger(
            "packet rx "
            f"type={data.get('payload_type_name')}({data.get('payload_type')}) "
            f"rssi={data.get('rssi')} snr={data.get('snr')} "
            f"sender={data.get('sender_name') or data.get('sender_id') or '?'}"
        )
        emit({"type": "packet", "data": data})

    async def on_raw_packet(
        packet: Any, raw_data: bytes, analysis: dict[str, Any] | None = None
    ) -> None:
        data = packet_to_dict(packet)
        raw_hex = raw_data.hex()
        logger(
            "raw packet rx "
            f"type={data.get('payload_type_name')}({data.get('payload_type')}) "
            f"bytes={len(raw_data)} raw={raw_hex[:64]}..."
        )
        emit(
            {
                "type": "raw_packet",
                "data": {
                    **data,
                    "raw_hex": raw_hex,
                    "raw_hex_preview": raw_hex[:64],
                    "analysis": analysis or {},
                },
            }
        )

    dispatcher.set_packet_received_callback(on_packet)
    logger("registered dispatcher packet callback")

    dispatcher.set_raw_packet_callback(on_raw_packet)
    logger("registered dispatcher raw packet callback")
