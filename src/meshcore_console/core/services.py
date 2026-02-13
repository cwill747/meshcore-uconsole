from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from meshcore_console.core.models import Channel, DeviceStatus, Message, Peer
from meshcore_console.core.types import MeshEventDict, SendResultDict

if TYPE_CHECKING:
    from meshcore_console.meshcore.settings import MeshcoreSettings


class MeshcoreService(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def get_status(self) -> DeviceStatus: ...

    def list_peers(self) -> list[Peer]: ...

    def list_messages(self, limit: int = 50) -> list[Message]: ...

    def list_channels(self) -> list[Channel]: ...

    def ensure_channel(self, channel_id: str, display_name: str | None = None) -> Channel:
        """Ensure a channel exists, creating it if necessary."""
        ...

    def list_messages_for_channel(self, channel_id: str, limit: int = 50) -> list[Message]: ...

    def send_message(self, peer_id: str, body: str) -> Message: ...

    def send_advert(
        self, name: str | None = None, *, route_type: str = "flood"
    ) -> SendResultDict: ...

    def poll_events(self, limit: int = 50) -> list[MeshEventDict]: ...

    def list_stored_packets(self, limit: int = 100) -> list[MeshEventDict]:
        """Return packets from persistent storage."""
        ...

    def list_recent_events(self, limit: int = 50) -> list[MeshEventDict]: ...

    def get_settings(self) -> MeshcoreSettings: ...

    def update_settings(self, settings: MeshcoreSettings) -> None: ...

    def get_device_location(self) -> tuple[float, float] | None: ...

    def is_mock_mode(self) -> bool:
        """Return True if running in mock mode."""
        ...

    def cycle_mock_gps(self) -> bool:
        """Cycle to next mock GPS position. No-op if not in mock mode."""
        ...

    def poll_gps(self) -> bool:
        """Poll GPS for new data. Call periodically from UI.

        Returns True to continue polling.
        """
        ...

    def get_gps_error(self) -> str | None:
        """Get the last GPS error message, if any."""
        ...

    def has_gps_fix(self) -> bool:
        """Return True if GPS has acquired a satellite fix."""
        ...

    def get_self_public_key(self) -> str | None:
        """Return this node's public key as a hex string, or None if unavailable."""
        ...
