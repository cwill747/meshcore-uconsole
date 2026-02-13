"""Type definitions for meshcore_console.

This module provides TypedDicts for packet/event data and Protocol stubs
for pyMC_core types to enable static typing without runtime dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

if TYPE_CHECKING:
    pass


# =============================================================================
# TypedDicts for structured data
# =============================================================================


class PacketData:
    """Packet data dictionary returned by packet_to_dict().

    Using class with __annotations__ instead of TypedDict for compatibility
    with total=False semantics while keeping required fields.
    """

    # Required fields (always set by packet_to_dict)
    path_len: int
    path_hops: list[str]
    raw: str

    # Optional fields (may be None depending on packet type)
    payload_type: int | None
    payload_type_name: str | None
    route_type: int | None
    route_type_name: str | None
    payload_len: int | None
    header: object | None
    snr: float | None
    rssi: int | None
    payload_text: str | None
    payload_hex: str | None
    sender_name: str | None
    sender_id: str | None
    sender_pubkey: str | None
    channel_name: str | None
    advert_name: str | None
    advert_lat: float | None
    advert_lon: float | None
    packet_hash: str | None


# Use dict[str, Any] as the actual type since TypedDict with mixed
# required/optional fields is complex. The PacketData class above
# documents the expected structure.
PacketDataDict = dict[str, Any]


class MeshEvent:
    """Mesh event structure emitted by the event bridge.

    Events always have 'type' and 'data' keys.
    """

    type: str
    data: dict[str, Any]


# Actual runtime type
MeshEventDict = dict[str, Any]


class SendResult:
    """Result of a send_advert operation."""

    success: bool
    tx_metadata: object | None
    dispatcher_result: bool


SendResultDict = dict[str, Any]


class SessionStatus:
    """Session status information."""

    connected: bool
    node_name: str
    board: str
    pymc_core_version: str


SessionStatusDict = dict[str, Any]


# =============================================================================
# Protocol stubs for pyMC_core types
# =============================================================================
# These protocols define the interface we use from pyMC_core without
# requiring the actual library to be installed (for mock mode, macOS dev).


class SX1262RadioProtocol(Protocol):
    """Protocol for pyMC_core SX1262Radio."""

    def begin(self) -> bool:
        """Initialize the radio hardware. Returns True on success."""
        ...

    def cleanup(self) -> None:
        """Clean up radio resources."""
        ...


class LocalIdentityProtocol(Protocol):
    """Protocol for pyMC_core LocalIdentity.

    Opaque handle representing the node's identity.
    """

    pass


class DispatcherProtocol(Protocol):
    """Protocol for pyMC_core dispatcher."""

    def set_packet_received_callback(self, cb: Callable[..., Awaitable[None]]) -> None:
        """Set callback for received packets."""
        ...

    def set_raw_packet_callback(self, cb: Callable[..., Awaitable[None]]) -> None:
        """Set callback for raw packets."""
        ...

    async def send_packet(self, packet: object, wait_for_ack: bool = False) -> object:
        """Send a packet through the mesh."""
        ...


class MeshNodeProtocol(Protocol):
    """Protocol for pyMC_core MeshNode."""

    dispatcher: DispatcherProtocol

    def set_event_service(self, service: EventServiceProtocol) -> None:
        """Set the event service for this node."""
        ...

    async def start(self) -> None:
        """Start the mesh node."""
        ...

    def stop(self) -> object:
        """Stop the mesh node. May return awaitable."""
        ...

    async def send_text(self, peer_name: str, message: str) -> object:
        """Send a text message to a peer."""
        ...

    async def send_group_text(self, channel_name: str, message: str) -> object:
        """Send a text message to a group channel."""
        ...


class EventSubscriberProtocol(Protocol):
    """Protocol for pyMC_core EventSubscriber."""

    pass


class EventServiceProtocol(Protocol):
    """Protocol for pyMC_core EventService."""

    def subscribe_all(self, subscriber: EventSubscriberProtocol) -> None:
        """Subscribe to all events."""
        ...

    def unsubscribe_all(self, subscriber: EventSubscriberProtocol) -> None:
        """Unsubscribe from all events."""
        ...


# Type alias for emit callback used throughout the codebase
EmitCallback = Callable[[MeshEventDict], None]

# Type alias for logger callback
LoggerCallback = Callable[[str], None]
