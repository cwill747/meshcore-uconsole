"""Mock MeshcoreClient for testing and development."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable
from uuid import uuid4

from meshcore_console.core.enums import PayloadType
from meshcore_console.core.models import Channel, DeviceStatus, Message, Peer
from meshcore_console.core.services import MeshcoreService
from meshcore_console.meshcore.config import runtime_config_from_settings
from meshcore_console.meshcore.settings import MeshcoreSettings

from .data import (
    create_mock_boot_events,
    create_mock_channels,
    create_mock_messages,
    create_mock_peers,
)
from .gps import MockGps
from .session import MockPyMCCoreSession


class MockMeshcoreClient(MeshcoreService):
    """Mock implementation of MeshcoreService for testing and development."""

    def __init__(self, node_name: str = "uconsole-node") -> None:
        self._settings = MeshcoreSettings(node_name=node_name)
        self._config = runtime_config_from_settings(self._settings)
        self._session = MockPyMCCoreSession(self._config)
        self._gps_provider = MockGps()
        self._connected = True
        self._event_notify: Callable[[], None] | None = None
        self._event_buffer: list[dict] = []
        self._event_history: list[dict] = []

        # Initialize mock state
        self._channels = create_mock_channels()
        self._peers = create_mock_peers()
        self._messages = create_mock_messages()
        self._event_buffer.extend(create_mock_boot_events())

        # Start GPS
        self._gps_provider.start()

    def connect(self) -> None:
        self._connected = True
        self._gps_provider.start()

    def disconnect(self) -> None:
        self._connected = False
        self._gps_provider.stop()

    def get_status(self) -> DeviceStatus:
        return DeviceStatus(
            node_id="mock-node",
            connected=self._connected,
            rssi=-69 if self._connected else None,
            battery_percent=87 if self._connected else None,
            last_seen=datetime.now(UTC),
        )

    def list_peers(self) -> list[Peer]:
        return sorted(self._peers.values(), key=lambda p: p.display_name)

    def list_messages(self, limit: int = 50) -> list[Message]:
        return self._messages[-limit:]

    def list_channels(self) -> list[Channel]:
        if not self._channels:
            channel = Channel(channel_id="public", display_name="#public", unread_count=0)
            self._channels["public"] = channel
        return sorted(self._channels.values(), key=lambda c: c.display_name.lower())

    def ensure_channel(self, channel_id: str, display_name: str | None = None) -> Channel:
        """Ensure a channel exists, creating it if necessary."""
        is_group = (
            channel_id == "public"
            or channel_id.startswith("#")
            or (display_name is not None and display_name.startswith("#"))
        )
        normalized_id = channel_id if is_group else channel_id.lower()
        if normalized_id in self._channels:
            return self._channels[normalized_id]
        channel = Channel(
            channel_id=normalized_id,
            display_name=display_name or (f"#{channel_id}" if is_group else channel_id),
            unread_count=0,
            peer_name=channel_id if not is_group else None,
            kind="group" if is_group else "dm",
        )
        self._channels[normalized_id] = channel
        return channel

    def list_messages_for_channel(self, channel_id: str, limit: int = 50) -> list[Message]:
        filtered = [m for m in self._messages if m.channel_id == channel_id]
        return filtered[-limit:]

    def remove_channel(self, channel_id: str) -> bool:
        """Remove a channel and its messages. Returns False if channel cannot be removed."""
        if channel_id == "public":
            return False
        self._channels.pop(channel_id, None)
        self._messages = [m for m in self._messages if m.channel_id != channel_id]
        return True

    def mark_channel_read(self, channel_id: str) -> None:
        if channel_id in self._channels:
            self._channels[channel_id].unread_count = 0

    def send_message(self, peer_id: str, body: str) -> Message:
        existing = self._channels.get(peer_id.lower()) or self._channels.get(peer_id)
        if existing is not None:
            is_group = existing.kind == "group"
        else:
            is_group = peer_id == "public" or peer_id.startswith("#")
        channel_id = peer_id if is_group else peer_id.lower()
        if channel_id not in self._channels:
            display = f"#{channel_id}" if is_group else peer_id
            self._channels[channel_id] = Channel(
                channel_id=channel_id,
                display_name=display,
                peer_name=peer_id if not is_group else None,
                kind="group" if is_group else "dm",
            )
        message = Message(
            message_id=str(uuid4()),
            sender_id=self._settings.node_name,
            body=body,
            channel_id=channel_id,
            created_at=datetime.now(UTC),
        )
        self._messages.append(message)
        if peer_id not in self._peers:
            self._peers[peer_id] = Peer(peer_id=peer_id, display_name=peer_id, signal_quality=None)

        self._append_event(
            {
                "type": "message_sent",
                "data": {
                    "peer_id": peer_id,
                    "channel_id": channel_id,
                    "body": body,
                    "at": message.created_at.isoformat(),
                },
            }
        )
        return message

    def send_advert(self, name: str | None = None, *, route_type: str = "flood") -> dict:
        self._append_event(
            {
                "type": "advert_sent",
                "data": {
                    "name": name or self._settings.node_name,
                    "route_type": route_type,
                    "success": True,
                    "tx_metadata": {"mock": True},
                },
            }
        )
        return {"success": True, "tx_metadata": {"mock": True}, "dispatcher_result": True}

    def poll_events(self, limit: int = 50) -> list[dict]:
        events: list[dict] = []
        try:
            drained = self._session.drain_events(max_items=limit)
            if isinstance(drained, list):
                events.extend(drained)
        except (RuntimeError, OSError):
            pass

        if self._event_buffer:
            events.extend(self._event_buffer)
            self._event_buffer.clear()

        for event in events:
            self._append_history(event)
            self._process_event_for_messages(event)

        if len(events) > limit:
            return events[-limit:]
        return events

    def list_recent_events(self, limit: int = 50) -> list[dict]:
        if limit <= 0:
            return []
        return self._event_history[-limit:]

    def list_stored_packets(self, limit: int = 100) -> list[dict]:
        return []

    def flush_stores(self) -> None:
        """No-op for mock client (no persistent stores)."""

    def get_stored_packet_count(self) -> int:
        return 0

    def get_settings(self) -> MeshcoreSettings:
        return self._settings.clone()

    def update_settings(self, settings: MeshcoreSettings) -> None:
        self._settings = settings.clone()
        self._config = runtime_config_from_settings(self._settings)

    def get_device_location(self) -> tuple[float, float] | None:
        return self._gps_provider.get_location()

    def is_mock_mode(self) -> bool:
        return True

    def cycle_mock_gps(self) -> bool:
        """Cycle to next mock GPS position."""
        self._gps_provider.cycle_position()
        return True

    def poll_gps(self) -> bool:
        """Poll GPS for new data (no-op for mock)."""
        return True

    def get_gps_error(self) -> str | None:
        """Get the last GPS error message (always None for mock)."""
        return None

    def has_gps_fix(self) -> bool:
        """Return True if GPS has acquired a fix (always True for mock)."""
        return True

    def set_favorite(self, peer_id: str, favorite: bool) -> None:
        """Toggle the favorite flag on a peer."""
        for peer in self._peers.values():
            if peer.peer_id == peer_id:
                peer.is_favorite = favorite
                return

    def get_self_public_key(self) -> str | None:
        """Return a mock public key for testing."""
        return "6b547fd13630e0f7a6b167df23b9876543210abcdef0123456789abcdef0a619"

    def _process_event_for_messages(self, event: dict) -> None:
        """Convert incoming packet events into messages and channels."""
        data = event.get("data")
        if not isinstance(data, dict):
            return
        payload_type_name = data.get("payload_type_name", "")
        if payload_type_name not in (PayloadType.GRP_TXT, PayloadType.TXT_MSG):
            return
        sender_name = data.get("sender_name") or data.get("peer_name") or "Unknown"
        message_text = data.get("payload_text") or data.get("message_text") or ""
        if not message_text:
            return

        is_direct = payload_type_name == PayloadType.TXT_MSG
        if is_direct:
            channel_name = sender_name.lower()
        else:
            channel_name = (data.get("channel_name") or "public").lower()

        path_hops = data.get("path_hops", [])
        message = Message(
            message_id=str(uuid4()),
            sender_id=sender_name,
            body=message_text,
            channel_id=channel_name,
            created_at=datetime.now(UTC),
            is_outgoing=False,
            path_len=int(data.get("path_len") or 0),
            path_hops=list(path_hops) if path_hops else [],
            snr=float(data["snr"]) if data.get("snr") is not None else None,
            rssi=int(data["rssi"]) if data.get("rssi") is not None else None,
        )
        self._messages.append(message)

        if channel_name not in self._channels:
            display = sender_name if is_direct else f"#{channel_name}"
            self._channels[channel_name] = Channel(
                channel_id=channel_name,
                display_name=display,
                unread_count=1,
                peer_name=sender_name if is_direct else None,
                kind="dm" if is_direct else "group",
            )
        else:
            self._channels[channel_name].unread_count += 1

    def set_event_notify(self, notify_fn: Callable[[], None]) -> None:
        self._event_notify = notify_fn
        self._session.set_event_notify(notify_fn)

    def _append_event(self, event: dict) -> None:
        self._event_buffer.append(event)
        self._append_history(event)
        if self._event_notify is not None:
            try:
                self._event_notify()
            except Exception:  # noqa: BLE001
                pass

    def _append_history(self, event: dict) -> None:
        self._event_history.append(event)
        if len(self._event_history) > 500:
            self._event_history = self._event_history[-500:]
