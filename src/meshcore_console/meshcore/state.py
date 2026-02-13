from dataclasses import dataclass, field

from meshcore_console.core.models import DeviceStatus, Message, Peer
from meshcore_console.core.services import MeshcoreService


@dataclass(slots=True)
class AppState:
    status: DeviceStatus | None = None
    peers: list[Peer] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)

    def refresh(self, service: MeshcoreService) -> None:
        self.status = service.get_status()
        self.peers = service.list_peers()
        self.messages = service.list_messages()
