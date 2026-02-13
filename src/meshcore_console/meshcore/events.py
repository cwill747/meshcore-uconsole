from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class MeshEventType(StrEnum):
    NODE_CONNECTED = "node_connected"
    NODE_DISCONNECTED = "node_disconnected"
    MESSAGE_RECEIVED = "message_received"
    PEER_DISCOVERED = "peer_discovered"


@dataclass(slots=True)
class MeshEvent:
    type: MeshEventType
    payload: dict
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
