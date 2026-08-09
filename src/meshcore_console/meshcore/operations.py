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

TXT_TYPE_CLI_DATA = 1


def _resolve_contact(node: MeshNodeProtocol, peer_name: str) -> object:
    contacts = node.contacts
    if contacts is not None:
        contact = contacts.get_by_name(peer_name)
        if contact is not None:
            return contact
    raise RuntimeError(f"Contact '{peer_name}' not found")


async def send_text(*, node: MeshNodeProtocol, peer_name: str, message: str) -> dict:
    """Send a direct text message to a peer via PacketBuilder."""
    from openhop_core.protocol.packet_builder import PacketBuilder

    contact = _resolve_contact(node, peer_name)

    pkt, ack_crc = PacketBuilder.create_text_message(
        contact=contact,
        local_identity=node.identity,
        message=message,
    )
    success = await node.dispatcher.send_packet(pkt, wait_for_ack=True, expected_crc=ack_crc)
    return {"success": success, "crc": ack_crc}


async def send_group_text(*, node: MeshNodeProtocol, channel_name: str, message: str) -> dict:
    """Broadcast a text message to a group/public channel via PacketBuilder."""
    from openhop_core.protocol.packet_builder import PacketBuilder

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
    from openhop_core.protocol.packet_builder import PacketBuilder

    contact = _resolve_contact(node, contact_name)

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


async def send_login(
    *,
    node: MeshNodeProtocol,
    peer_name: str,
    password: str,
    timeout: float = 10.0,
) -> dict:
    """Send a login request to a repeater and wait for the response."""
    from openhop_core.protocol.packet_builder import PacketBuilder

    contact = _resolve_contact(node, peer_name)

    login_handler = node.dispatcher.login_response_handler
    dest_hash = bytes.fromhex(contact.public_key)[0]
    login_handler.store_login_password(dest_hash, password)

    login_event = asyncio.Event()
    login_result: dict = {"success": False, "data": {}}

    def _on_login(success: bool, data: dict) -> None:
        login_result["success"] = success
        login_result["data"] = data
        login_event.set()

    login_handler.set_login_callback(_on_login)
    try:
        pkt = PacketBuilder.create_login_packet(
            contact=contact,
            local_identity=node.identity,
            password=password,
        )
        await node.dispatcher.send_packet(pkt, wait_for_ack=False)
        await asyncio.wait_for(login_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"success": False, "reason": "Login response timeout"}
    finally:
        login_handler.set_login_callback(None)
        login_handler.clear_login_password(dest_hash)

    data = login_result["data"]
    return {
        "success": login_result["success"],
        "repeater": peer_name,
        "is_admin": data.get("is_admin", False),
        "keep_alive_interval": data.get("keep_alive_interval", 0),
        "acl_permissions": data.get("reserved", data.get("permissions", 0)),
        "firmware_ver_level": data.get("firmware_ver_level"),
        "reason": "Login successful" if login_result["success"] else "Login failed",
    }


async def send_logout(*, node: MeshNodeProtocol, peer_name: str) -> dict:
    """Send a logout/disconnect to a repeater."""
    from openhop_core.protocol.packet_builder import PacketBuilder

    contact = _resolve_contact(node, peer_name)

    pkt, _crc = PacketBuilder.create_logout_packet(
        contact=contact,
        local_identity=node.identity,
    )
    await node.dispatcher.send_packet(pkt, wait_for_ack=False)
    return {"success": True, "repeater": peer_name}


async def send_repeater_command(
    *,
    node: MeshNodeProtocol,
    peer_name: str,
    command: str,
    timeout: float = 15.0,
) -> dict:
    """Send a CLI command to a repeater and wait for the response."""
    from openhop_core.protocol.packet_builder import PacketBuilder

    contact = _resolve_contact(node, peer_name)

    text_handler = node.dispatcher.text_message_handler
    response_event = asyncio.Event()
    response_data: dict = {"text": None}

    expected_key = contact.public_key

    def _on_response(message_text: str, sender_contact: object) -> None:
        sender_key = getattr(sender_contact, "public_key", None)
        if sender_key is not None and sender_key != expected_key:
            return
        response_data["text"] = message_text
        response_event.set()

    text_handler.set_command_response_callback(_on_response)
    try:
        pkt, _crc = PacketBuilder.create_text_message(
            contact=contact,
            local_identity=node.identity,
            message=command,
            txt_type=TXT_TYPE_CLI_DATA,
        )
        await node.dispatcher.send_packet(pkt, wait_for_ack=False)
        await asyncio.wait_for(response_event.wait(), timeout=timeout)
        return {
            "success": True,
            "repeater": peer_name,
            "command": command,
            "response_text": response_data["text"],
        }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "repeater": peer_name,
            "command": command,
            "response_text": None,
            "reason": "Command response timeout",
        }
    finally:
        text_handler.set_command_response_callback(None)


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
    from openhop_core.protocol.packet_builder import PacketBuilder

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
