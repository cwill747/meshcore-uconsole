from __future__ import annotations

import asyncio
import copy
import struct
import time
from typing import Callable

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


def _route_type(contact: object) -> str:
    """Flood while the route is unknown (out_path_len -1), like firmware's
    sendMessage(); direct (incl. zero-hop) once a route is known.
    create_text_message() defaults to direct, which sent zero-hop DIRECT packets
    to contacts with no route, and nobody but a direct neighbour could hear them."""
    return "direct" if getattr(contact, "out_path_len", -1) >= 0 else "flood"


def _text_message_contact(contact: object) -> object:
    """Return a PacketBuilder-compatible contact without changing the contact book."""
    out_path = getattr(contact, "out_path", None)
    if not isinstance(out_path, (bytes, bytearray)):
        return contact
    normalized = copy.copy(contact)
    setattr(normalized, "out_path", list(out_path))
    return normalized


DM_ATTEMPTS = 3


async def send_text(
    *, node: MeshNodeProtocol, peer_name: str, message: str, attempts: int = DM_ATTEMPTS
) -> dict:
    """Send a direct text message and wait for its ACK, retrying like the MeshCore apps.

    Each retry carries the next attempt number with the same timestamp (the
    receiver drops duplicates; the expected ACK changes with the attempt).  If
    a known direct route fails twice, it is forgotten and the last attempt
    floods.  ``success`` is True only when an ACK arrived.
    """
    from openhop_core.protocol.packet_builder import PacketBuilder

    original = _resolve_contact(node, peer_name)
    timestamp = int(time.time())
    ack_crc = 0
    for attempt in range(attempts):
        if attempt == attempts - 1 and attempt > 0 and getattr(original, "out_path_len", -1) >= 0:
            original.out_path_len = -1  # type: ignore[attr-defined]  # route went stale
            original.out_path = []  # type: ignore[attr-defined]
        contact = _text_message_contact(original)
        pkt, ack_crc = PacketBuilder.create_text_message(
            contact=contact,
            local_identity=node.identity,
            message=message,
            attempt=attempt,
            message_type=_route_type(contact),
            timestamp=timestamp,
        )
        if await node.dispatcher.send_packet(pkt, wait_for_ack=True, expected_crc=ack_crc):
            return {"success": True, "attempts": attempt + 1, "crc": ack_crc}
    return {"success": False, "attempts": attempts, "crc": ack_crc}


async def send_group_text(
    *,
    node: MeshNodeProtocol,
    channel_name: str,
    message: str,
    region_scope: str | None = None,
) -> dict:
    """Broadcast a text message to a group/public channel via PacketBuilder.

    If ``region_scope`` is set, the packet is scoped to that region before
    dispatch. The scoped packet keeps its route type, so the dispatcher's
    app-wide default scope does not overwrite it. If ``region_scope`` is
    None, the dispatcher applies the default scope, if one is configured.
    """
    from openhop_core.protocol.packet_builder import PacketBuilder

    from .region import apply_region_scope

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
    if region_scope:
        apply_region_scope(pkt, region_scope)
    success = await node.dispatcher.send_packet(pkt, wait_for_ack=False)
    return {"success": success, "group": channel_name, "region_scope": region_scope}


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

    # The request timestamp is the tag the responder reflects; openhop_core
    # matches replies on (full public key, tag).
    pkt, tag = PacketBuilder.create_telem_request(
        contact=contact,
        local_identity=node.identity,
        want_base=True,
        want_location=want_location,
        want_environment=False,
    )

    pubkey = bytes.fromhex(contact.public_key)
    response_handler = node.dispatcher.protocol_response_handler

    response_event = asyncio.Event()
    response_data: dict = {}

    def _on_response(success: bool, text: str, parsed: dict) -> None:
        response_data["success"] = success
        response_data["text"] = text
        response_data["parsed"] = parsed
        response_event.set()

    response_handler.set_response_callback(pubkey, tag, _on_response)
    t0 = time.monotonic()
    try:
        await node.dispatcher.send_packet(pkt, wait_for_ack=False)
        got_response = await asyncio.wait_for(response_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        got_response = False
    finally:
        response_handler.clear_response_callback(pubkey, tag)

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


async def request_owner_info(
    *,
    node: MeshNodeProtocol,
    contact: object,
    timeout: float = 8.0,
) -> dict | None:
    """Anonymous ANON_REQ_TYPE_OWNER: a repeater answers "name\nowner", no login.

    Firmware only answers direct (``isRouteDirect``) requests, so ``contact``
    needs a known route (``out_path_len >= 0``; 0 for a zero-hop neighbour) and
    must be in the contact book for openhop_core to decrypt the reply.  Returns
    openhop's parsed reply dict, or None on timeout.
    """
    from openhop_core.protocol.constants import ANON_REQ_TYPE_OWNER
    from openhop_core.protocol.packet_builder import PacketBuilder

    # req_data = [type][reply path length byte][reply path...]; 0 = zero-hop reply
    pkt, tag = PacketBuilder.create_anon_request(
        contact, node.identity, req_data=bytes([ANON_REQ_TYPE_OWNER, 0x00])
    )
    pubkey = bytes.fromhex(contact.public_key)  # type: ignore[attr-defined]
    response_handler = node.dispatcher.protocol_response_handler
    reply: dict = {}
    done = asyncio.Event()

    def _on_response(_success: bool, _text: str, parsed: dict) -> None:
        reply.update(parsed)
        done.set()

    response_handler.set_response_callback(pubkey, tag, _on_response)
    try:
        await node.dispatcher.send_packet(pkt, wait_for_ack=False)
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        response_handler.clear_response_callback(pubkey, tag)
    return reply


async def request_repeater_data(
    *,
    node: MeshNodeProtocol,
    peer_name: str,
    req_type: int,
    data: bytes,
    timestamp: int,
    waiters: dict[bytes, Callable[[bytes], None]],
    timeout: float = 10.0,
) -> bytes | None:
    """A binary REQ to a logged-in repeater (status, neighbours, access list,
    owner info); returns the reply bytes after the reflected tag, or None.

    The reply comes back through openhop_core's binary response callback,
    which the session routes to ``waiters`` by tag: unlike the parsed reply a
    waiter gets, it carries the reply bytes unaltered (openhop_core's type
    guess would read a long neighbours reply as status and cut it to 56 bytes).
    """
    from openhop_core.protocol.packet_builder import PacketBuilder

    contact = _resolve_contact(node, peer_name)
    pkt, tag = PacketBuilder.create_protocol_request(
        contact, node.identity, protocol_code=req_type, data=data, timestamp=timestamp
    )
    key = struct.pack("<I", tag & 0xFFFFFFFF)
    reply: dict[str, bytes] = {}
    done = asyncio.Event()

    def _on_reply(payload: bytes) -> None:
        reply["data"] = payload
        done.set()

    waiters[key] = _on_reply
    try:
        await node.dispatcher.send_packet(pkt, wait_for_ack=False)
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        waiters.pop(key, None)
    return reply["data"]


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
    pubkey = bytes.fromhex(contact.public_key)
    dest_hash = pubkey[0]

    login_event = asyncio.Event()
    login_result: dict = {"success": False, "data": {}}

    def _on_login(success: bool, data: dict) -> None:
        login_result["success"] = success
        login_result["data"] = data
        login_event.set()

    login_handler.register_login_callback(pubkey, _on_login)
    try:
        login_handler.store_login_password(dest_hash, password)
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
        login_handler.remove_login_callback(pubkey, _on_login)
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
    """Forget the repeater session.  Local only: MeshCore firmware has no logout
    command (companion CMD_LOGOUT just drops the connection), and the old CLI
    "logout" only drew an "Unknown command" reply that surfaced as a DM."""
    _resolve_contact(node, peer_name)
    return {"success": True, "repeater": peer_name}


async def send_repeater_command(
    *,
    node: MeshNodeProtocol,
    peer_name: str,
    command: str,
    timeout: float = 15.0,
    timestamp: int | None = None,
) -> dict:
    """Send a CLI command to a repeater and wait for the response."""
    from openhop_core.protocol.packet_builder import PacketBuilder

    contact = _text_message_contact(_resolve_contact(node, peer_name))

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

    pubkey = bytes.fromhex(expected_key)
    text_handler.register_command_response(pubkey, _on_response)
    try:
        pkt, _crc = PacketBuilder.create_text_message(
            contact=contact,
            local_identity=node.identity,
            message=command,
            message_type=_route_type(contact),
            txt_type=TXT_TYPE_CLI_DATA,
            timestamp=timestamp,
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
        text_handler.unregister_command_response(pubkey, _on_response)


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
