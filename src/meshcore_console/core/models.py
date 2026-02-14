from dataclasses import dataclass, field
from datetime import UTC, datetime


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


@dataclass(slots=True)
class Message:
    message_id: str
    sender_id: str
    body: str
    channel_id: str = "public"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_outgoing: bool = False
    path_len: int = 0
    snr: float | None = None
    rssi: int | None = None


@dataclass(slots=True)
class Channel:
    channel_id: str
    display_name: str
    unread_count: int = 0
    peer_name: str | None = None  # Original-case peer name for DM channels
