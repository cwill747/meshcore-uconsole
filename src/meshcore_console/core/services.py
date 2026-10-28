from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol

from meshcore_console.core.models import Channel, DeviceStatus, Message, Peer, RepeaterLoginState
from meshcore_console.core.types import MeshEventDict, SendResultDict

if TYPE_CHECKING:
    from meshcore_console.meshcore.settings import MeshcoreSettings


class MeshcoreService(Protocol):
    def set_event_notify(self, notify_fn: Callable[[], None]) -> None: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def get_status(self) -> DeviceStatus: ...

    def list_peers(self) -> list[Peer]: ...

    def list_messages(self, limit: int = 50) -> list[Message]: ...

    def list_channels(self) -> list[Channel]: ...

    def ensure_channel(self, channel_id: str, display_name: str | None = None) -> Channel:
        """Ensure a channel exists, creating it if necessary."""
        ...

    def import_channel(self, channel_id: str, display_name: str, secret: str) -> Channel:
        """Create a private channel with an explicit PSK (issue #101).

        Raises ValueError if the secret is not 32 hex characters.
        """
        ...

    def remove_channel(self, channel_id: str) -> bool:
        """Remove a channel and its messages. Returns False if channel cannot be removed."""
        ...

    def mark_channel_read(self, channel_id: str) -> None:
        """Reset the unread count for a channel to zero."""
        ...

    def get_channel_region_scope(self, channel_id: str) -> str | None:
        """Return the region scope for a group channel, or None if unset."""
        ...

    def set_channel_region_scope(self, channel_id: str, scope: str | None) -> None:
        """Set or clear the region scope for a group channel (issue #90)."""
        ...

    def list_messages_for_channel(self, channel_id: str, limit: int = 50) -> list[Message]: ...

    def send_message(self, peer_id: str, body: str) -> Message: ...

    def is_private_channel(self, channel_id: str) -> bool:
        """True for a group channel joined with its own (non-derivable) key."""
        ...

    def ensure_dm_channel(self, peer_name: str) -> Channel:
        """The DM thread with ``peer_name`` (channel id ``dm:<name>``), created if needed."""
        ...

    def send_advert(
        self, name: str | None = None, *, route_type: str = "flood"
    ) -> SendResultDict: ...

    def discover_nodes(self) -> list[dict]:
        """Zero-hop discover of nearby repeaters/sensors; blocks for the answer window."""
        ...

    def poll_events(self, limit: int = 50) -> list[MeshEventDict]: ...

    def list_stored_packets(self, limit: int = 100) -> list[MeshEventDict]:
        """Return packets from persistent storage."""
        ...

    def list_recent_events(self, limit: int = 50) -> list[MeshEventDict]: ...

    def get_settings(self) -> MeshcoreSettings: ...

    def update_settings(self, settings: MeshcoreSettings) -> None: ...

    def get_device_location(self) -> tuple[float, float] | None: ...

    def get_map_view(self) -> tuple[float, float, float] | None:
        """The map's last view (latitude, longitude, zoom), if one was saved."""
        ...

    def save_map_view(self, lat: float, lon: float, zoom: float) -> None: ...

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

    def flush_stores(self) -> None:
        """Flush any dirty stores to disk."""
        ...

    def get_gps_error(self) -> str | None:
        """Get the last GPS error message, if any."""
        ...

    def has_gps_fix(self) -> bool:
        """Return True if GPS has acquired a satellite fix."""
        ...

    def set_favorite(self, peer_id: str, favorite: bool) -> None:
        """Toggle the favorite flag on a peer."""
        ...

    def request_telemetry(self, peer_name: str) -> dict:
        """Request telemetry data from a remote peer. Returns decoded sensor data."""
        ...

    def get_self_public_key(self) -> str | None:
        """Return this node's public key as a hex string, or None if unavailable."""
        ...

    # -- Repeater admin --------------------------------------------------------

    def login_to_repeater(
        self, peer_name: str, password: str, *, save_password: bool = False
    ) -> dict: ...

    def guest_login_to_repeater(self, peer_name: str) -> dict: ...

    def logout_from_repeater(self, peer_name: str) -> None: ...

    def send_repeater_command(self, peer_name: str, command: str) -> dict: ...

    def get_repeater_status(self, peer_name: str) -> dict | None:
        """Repeater counters (battery, uptime, airtime, noise floor, packets); None on no reply."""
        ...

    def get_repeater_owner_info(self, peer_name: str) -> dict | None:
        """{"version", "name", "owner_info"} from the repeater; None on no reply."""
        ...

    def get_repeater_neighbours(self, peer_name: str) -> list[dict] | None:
        """[{"pubkey" (6-byte prefix), "name" or None, "secs_ago", "snr"}], newest first."""
        ...

    def get_repeater_access_list(self, peer_name: str) -> list[dict] | None:
        """[{"key" (6-byte prefix), "role", "name" or None}]; admin login only."""
        ...

    def get_repeater_login_state(self, peer_name: str) -> RepeaterLoginState | None: ...

    def list_logged_in_repeaters(self) -> list[str]: ...

    def get_saved_repeater_password(self, peer_name: str) -> str | None: ...

    def delete_saved_repeater_password(self, peer_name: str) -> None: ...
