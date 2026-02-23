from __future__ import annotations

from meshcore_console.core.types import (
    EmitCallback,
    LocalIdentityProtocol,
    LoggerCallback,
    MeshNodeProtocol,
    SendResultDict,
)


async def send_text(*, node: MeshNodeProtocol, peer_name: str, message: str) -> object:
    return await node.send_text(peer_name, message)


async def send_group_text(*, node: MeshNodeProtocol, channel_name: str, message: str) -> object:
    """Broadcast a text message to a group/public channel."""
    return await node.send_group_text(channel_name, message)


async def request_telemetry(
    *,
    node: MeshNodeProtocol,
    contact_name: str,
    want_location: bool = True,
    timeout: float = 10.0,
) -> dict:
    """Request telemetry data from a remote peer."""
    return await node.send_telemetry_request(
        contact_name,
        want_base=True,
        want_location=want_location,
        want_environment=False,
        timeout=timeout,
    )


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
