from dataclasses import dataclass, field
from datetime import UTC, datetime


# DM threads live in their own channel-id namespace.  They used to share it with
# group channels (both were the lowercased name), so a DM from "Test" landed in
# #test and the reply was broadcast there, and "Message" on a node named
# "Public" posted to the Public channel.
DM_PREFIX = "dm:"


def dm_channel_id(peer_name: str) -> str:
    """The channel id of the DM thread with ``peer_name``."""
    return DM_PREFIX + peer_name.lower()


@dataclass(slots=True)
class DeviceStatus:
    node_id: str
    connected: bool
    rssi: int | None = None
    battery_percent: int | None = None
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class Peer:
    peer_id: str
    display_name: str
    signal_quality: int | None = None
    public_key: str | None = None  # Full 64-char hex public key
    last_advert_time: datetime | None = None
    last_path: list[str] = field(default_factory=list)  # Path hops as hex strings
    is_repeater: bool = False  # True if this is a network/repeater node
    rssi: int | None = None
    snr: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_updated: datetime | None = None
    is_favorite: bool = False


@dataclass(slots=True)
class Message:
    message_id: str
    sender_id: str
    body: str
    channel_id: str = "public"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_outgoing: bool = False
    path_len: int = 0
    path_hops: list[str] = field(default_factory=list)
    snr: float | None = None
    rssi: int | None = None
    # Outgoing DMs: True once ACKed, False if no ACK came; None otherwise (not stored).
    delivered: bool | None = None


@dataclass(slots=True)
class RepeaterLoginState:
    peer_name: str
    is_admin: bool = False
    is_guest: bool = False
    keep_alive_interval: int = 0
    acl_permissions: int = 0
    firmware_ver_level: int | None = None
    logged_in_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class Channel:
    channel_id: str
    display_name: str
    unread_count: int = 0
    peer_name: str | None = None  # Original-case peer name for DM channels
    kind: str = "group"  # "group" or "dm"
