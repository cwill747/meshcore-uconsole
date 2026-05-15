from __future__ import annotations

import asyncio
import time

from meshcore_console.core.types import (
    EmitCallback,
    LocalIdentityProtocol,
    LoggerCallback,
    MeshNodeProtocol,
    SendResultDict,
)


async def send_text(*, node: MeshNodeProtocol, peer_name: str, message: str) -> dict:
    """Send a direct text message to a peer via PacketBuilder."""
    from pymc_core.protocol.packet_builder import PacketBuilder

    contacts = node.contacts
    contact = None
    if contacts is not None:
        contact = contacts.get_by_name(peer_name)
    if contact is None:
        raise RuntimeError(f"Contact '{peer_name}' not found")

    pkt, ack_crc = PacketBuilder.create_text_message(
        contact=contact,
        local_identity=node.identity,
        message=message,
    )
    success = await node.dispatcher.send_packet(pkt, wait_for_ack=True, expected_crc=ack_crc)
    return {"success": success, "crc": ack_crc}


async def send_group_text(*, node: MeshNodeProtocol, channel_name: str, message: str) -> dict:
    """Broadcast a text message to a group/public channel via PacketBuilder."""
    from pymc_core.protocol.packet_builder import PacketBuilder

    if node.channel_db is None:
        raise RuntimeError("No channel database configured")
    channels_config = node.channel_db.get_channels()

    pkt = PacketBuilder.create_group_datagram(
        group_name=channel_name,
        local_identity=node.identity,
        message=message,
        sender_name=node.node_name,
        channels_config=channels_config,
    )
    success = await node.dispatcher.send_packet(pkt, wait_for_ack=False)
    return {"success": success, "group": channel_name}


async def request_telemetry(
    *,
    node: MeshNodeProtocol,
    contact_name: str,
    want_location: bool = True,
    timeout: float = 10.0,
) -> dict:
    """Request telemetry data from a remote peer via PacketBuilder."""
    from pymc_core.protocol.packet_builder import PacketBuilder

    contacts = node.contacts
    contact = None
    if contacts is not None:
        contact = contacts.get_by_name(contact_name)
    if contact is None:
        raise RuntimeError(f"Contact '{contact_name}' not found")

    pkt, _ts = PacketBuilder.create_telem_request(
        contact=contact,
        local_identity=node.identity,
        want_base=True,
        want_location=want_location,
        want_environment=False,
    )

    contact_hash = bytes.fromhex(contact.public_key)[0]
    response_handler = node.dispatcher.protocol_response_handler

    response_event = asyncio.Event()
    response_data: dict = {}

    def _on_response(success: bool, text: str, parsed: dict) -> None:
        response_data["success"] = success
        response_data["text"] = text
        response_data["parsed"] = parsed
        response_event.set()

    response_handler.set_response_callback(contact_hash, _on_response)
    t0 = time.monotonic()
    try:
        await node.dispatcher.send_packet(pkt, wait_for_ack=False)
        got_response = await asyncio.wait_for(response_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        got_response = False
    finally:
        response_handler.clear_response_callback(contact_hash)

    rtt = (time.monotonic() - t0) * 1000

    if not got_response:
        return {
            "success": False,
            "contact": contact_name,
            "requested": {"base": True, "location": want_location, "environment": False},
            "telemetry_data": None,
            "rtt_ms": round(rtt, 2),
            "reason": f"Telemetry response timeout after {timeout}s",
        }

    return {
        "success": response_data.get("success", False),
        "contact": contact_name,
        "requested": {"base": True, "location": want_location, "environment": False},
        "telemetry_data": response_data.get("parsed"),
        "response_text": response_data.get("text"),
        "rtt_ms": round(rtt, 2),
        "reason": (
            "Telemetry response received"
            if response_data.get("success")
            else "Telemetry request failed"
        ),
    }


async def send_advert(
    *,
    node: MeshNodeProtocol,
    identity: LocalIdentityProtocol,
    default_name: str,
    emit: EmitCallback,
    logger: LoggerCallback,
    name: str | None = None,
    lat: float = 0.0,
    lon: float = 0.0,
    route_type: str = "flood",
) -> SendResultDict:
    from pymc_core.protocol.packet_builder import PacketBuilder

    advert_name = name or default_name
    packet = PacketBuilder.create_self_advert(
        local_identity=identity,
        name=advert_name,
        lat=lat,
        lon=lon,
        route_type=route_type,
    )
    dispatcher_result = await node.dispatcher.send_packet(packet, wait_for_ack=False)
    tx_metadata = getattr(packet, "_tx_metadata", None)
    success = bool(dispatcher_result) and tx_metadata is not None
    if not success:
        logger(
            "advert transmission reported unsuccessful: "
            f"dispatcher_result={dispatcher_result} tx_metadata={tx_metadata}"
        )

    emit(
        {
            "type": "advert_sent",
            "data": {
                "name": advert_name,
                "lat": lat,
                "lon": lon,
                "route_type": route_type,
                "success": success,
                "tx_metadata": tx_metadata,
                "dispatcher_result": bool(dispatcher_result),
            },
        }
    )
    return {
        "success": success,
        "tx_metadata": tx_metadata,
        "dispatcher_result": bool(dispatcher_result),
    }
