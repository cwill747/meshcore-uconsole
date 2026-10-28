"""Mock MeshcoreClient for testing and development."""

from __future__ import annotations

import time
from collections import deque
from datetime import UTC, datetime
from typing import Callable
from uuid import uuid4

from meshcore_console.core.enums import EventType, PayloadType
from meshcore_console.core.models import (
    DM_PREFIX,
    Channel,
    DeviceStatus,
    Message,
    Peer,
    RepeaterLoginState,
    dm_channel_id,
)
from meshcore_console.core.services import MeshcoreService
from meshcore_console.meshcore.config import runtime_config_from_settings
from meshcore_console.meshcore.region import normalize_region_scope
from meshcore_console.meshcore.settings import MeshcoreSettings

from .data import (
    MOCK_CLI_RESPONSES,
    create_mock_boot_events,
    create_mock_channels,
    create_mock_messages,
    create_mock_peers,
)
from .gps import MockGps
from .session import MockOpenHopCoreSession


class MockMeshcoreClient(MeshcoreService):
    """Mock implementation of MeshcoreService for testing and development."""

    def __init__(self, node_name: str = "uconsole-node") -> None:
        self._settings = MeshcoreSettings(node_name=node_name)
        self._config = runtime_config_from_settings(self._settings)
        self._session = MockOpenHopCoreSession(self._config)
        self._gps_provider = MockGps()
        self._connected = True
        self._event_notify: Callable[[], None] | None = None
        self._event_buffer: deque[dict] = deque()
        self._event_history: list[dict] = []
        self._map_view: tuple[float, float, float] | None = None
        self._mock_repeater_settings = {
            "name": "Hilltop",
            "lat": "51.3397",
            "lon": "12.3731",
            "owner.info": "Mock owner|mock@example.org",
            "advert.interval": "0",
            "flood.advert.interval": "47",
            "repeat": "on",
            "flood.max": "64",
            "flood.max.advert": "8",
            "flood.max.unscoped": "64",
            "loop.detect": "minimal",
            "path.hash.mode": "0",
            "multi.acks": "0",
            "af": "1.0",
            "rxdelay": "0.0",
            "txdelay": "0.5",
            "direct.txdelay": "0.2",
            "radio": "869.618,62.5,8,8",
            "tx": "22",
            "allow.read.only": "off",
            "guest.password": "",
        }

        self._repeater_sessions: dict[str, RepeaterLoginState] = {}
        self._saved_passwords: dict[str, str] = {}
        self._channel_scopes: dict[str, str] = {}
        self._channel_secrets: dict[str, str] = {}

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

    def is_private_channel(self, channel_id: str) -> bool:
        return False

    def ensure_dm_channel(self, peer_name: str) -> Channel:
        channel_id = dm_channel_id(peer_name)
        if channel_id not in self._channels:
            self._channels[channel_id] = Channel(
                channel_id=channel_id,
                display_name=peer_name,
                unread_count=0,
                peer_name=peer_name,
                kind="dm",
            )
        return self._channels[channel_id]

    def ensure_channel(self, channel_id: str, display_name: str | None = None) -> Channel:
        """Ensure a channel exists, creating it if necessary (same rules as the real client)."""
        if channel_id in self._channels:
            return self._channels[channel_id]
        if channel_id.startswith(DM_PREFIX):
            return self.ensure_dm_channel(display_name or channel_id[len(DM_PREFIX) :])
        is_group = (
            channel_id == "public"
            or channel_id.startswith("#")
            or (display_name is not None and display_name.startswith("#"))
        )
        if not is_group:
            return self.ensure_dm_channel(channel_id)
        channel = Channel(
            channel_id=channel_id,
            display_name=display_name or f"#{channel_id}",
            unread_count=0,
            kind="group",
        )
        self._channels[channel_id] = channel
        return channel

    def import_channel(self, channel_id: str, display_name: str, secret: str) -> Channel:
        """Create a private channel with an explicit PSK (issue #101)."""
        from meshcore_console.meshcore.channel_db import (
            normalize_channel_name,
            normalize_channel_secret,
        )

        clean_secret = normalize_channel_secret(secret)
        name = normalize_channel_name(display_name or channel_id)
        if not name:
            raise ValueError("Channel name is required")
        if name == "public":
            raise ValueError("The Public channel secret cannot be replaced")
        existing = self._channels.get(name)
        if existing is not None and existing.kind == "dm":
            raise ValueError(
                f"A direct message named '{name}' already exists; choose a different channel name"
            )
        self._channel_secrets[name] = clean_secret
        channel = Channel(
            channel_id=name,
            display_name=f"#{name}",
            unread_count=0,
            kind="group",
        )
        self._channels[name] = channel
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

    def get_channel_region_scope(self, channel_id: str) -> str | None:
        return self._channel_scopes.get(channel_id)

    def set_channel_region_scope(self, channel_id: str, scope: str | None) -> None:
        normalized = normalize_region_scope(scope)
        if normalized is None:
            self._channel_scopes.pop(channel_id, None)
        else:
            self._channel_scopes[channel_id] = normalized

    def send_message(self, peer_id: str, body: str) -> Message:
        channel = self._channels.get(peer_id) or self.ensure_channel(peer_id)
        channel_id = channel.channel_id
        message = Message(
            message_id=str(uuid4()),
            sender_id=self._settings.node_name,
            body=body,
            channel_id=channel_id,
            created_at=datetime.now(UTC),
            is_outgoing=True,
            delivered=True if channel.kind == "dm" else None,
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

    def discover_nodes(self) -> list[dict]:
        time.sleep(1.0)  # stands in for the answer window
        return [
            {
                "pub_key": "13ab5c7e" + "00" * 28,
                "node_type": 2,
                "snr_here": 11.5,
                "snr_there": 12.0,
                "rssi": -30,
                "name": "Repeater 13ab5c7e",
            }
        ]

    def poll_events(self, limit: int = 50) -> list[dict]:
        events: list[dict] = []
        try:
            drained = self._session.drain_events(max_items=limit)
            if isinstance(drained, list):
                events.extend(drained)
        except (RuntimeError, OSError):
            pass

        while self._event_buffer:
            events.append(self._event_buffer.popleft())

        for event in events:
            self._append_history(event)
            self._process_event_for_messages(event)

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

    def get_map_view(self) -> tuple[float, float, float] | None:
        return self._map_view

    def save_map_view(self, lat: float, lon: float, zoom: float) -> None:
        self._map_view = (lat, lon, zoom)

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

    def request_telemetry(self, peer_name: str) -> dict:
        """Return synthetic telemetry data matching openhop_core's format."""
        import time

        loc = self._gps_provider.get_location()
        lat = (loc[0] + 0.012) if loc else 37.7749
        lon = (loc[1] - 0.008) if loc else -122.4194
        result = {
            "success": True,
            "contact": peer_name,
            "telemetry_data": {
                "type": "telemetry",
                "reflected_timestamp": int(time.time()),
                "sensor_count": 2,
                "sensors": [
                    {
                        "channel": 1,
                        "type": "Voltage",
                        "type_id": "Voltage",
                        "value": 3.87,
                        "raw_value": "01740183",
                    },
                    {
                        "channel": 2,
                        "type": "Location",
                        "type_id": "Location",
                        "value": {"latitude": lat, "longitude": lon, "altitude": 42.5},
                        "raw_value": "",
                    },
                ],
            },
            "rtt_ms": 1247.0,
            "reason": "Telemetry response received",
        }
        self._append_event(
            {
                "type": "telemetry_received",
                "data": {"peer_name": peer_name, "telemetry": result},
            }
        )
        return result

    def get_self_public_key(self) -> str | None:
        """Return a mock public key for testing."""
        return "6b547fd13630e0f7a6b167df23b9876543210abcdef0123456789abcdef0a619"

    # -- Repeater admin (mock) -------------------------------------------------

    def login_to_repeater(
        self, peer_name: str, password: str, *, save_password: bool = False
    ) -> dict:
        is_admin = password != ""
        state = RepeaterLoginState(
            peer_name=peer_name,
            is_admin=is_admin,
            is_guest=not is_admin,
            keep_alive_interval=300,
            acl_permissions=0x02 if is_admin else 0x01,
            firmware_ver_level=12,
        )
        self._repeater_sessions[peer_name] = state
        if save_password and password:
            self._saved_passwords[peer_name] = password
        self._append_event(
            {
                "type": EventType.REPEATER_LOGIN,
                "data": {"peer_name": peer_name, "success": True, "is_admin": is_admin},
            }
        )
        return {
            "success": True,
            "repeater": peer_name,
            "is_admin": is_admin,
            "keep_alive_interval": 300,
            "acl_permissions": 0x02 if is_admin else 0x01,
            "firmware_ver_level": 12,
            "reason": "Login successful",
        }

    def guest_login_to_repeater(self, peer_name: str) -> dict:
        return self.login_to_repeater(peer_name, password="")

    def logout_from_repeater(self, peer_name: str) -> None:
        self._repeater_sessions.pop(peer_name, None)
        self._append_event({"type": EventType.REPEATER_LOGOUT, "data": {"peer_name": peer_name}})

    def send_repeater_command(self, peer_name: str, command: str) -> dict:
        stripped = command.strip().lower()
        cmd_key = stripped.split()[0] if stripped else ""
        response_text = self._mock_setting_reply(command.strip()) or (
            MOCK_CLI_RESPONSES.get(stripped) or MOCK_CLI_RESPONSES.get(cmd_key, f"OK: {command}")
        )
        self._append_event(
            {
                "type": EventType.REPEATER_COMMAND_RESPONSE,
                "data": {
                    "peer_name": peer_name,
                    "command": command,
                    "success": True,
                    "response_text": response_text,
                },
            }
        )
        return {
            "success": True,
            "repeater": peer_name,
            "command": command,
            "response_text": response_text,
        }

    def _mock_setting_reply(self, command: str) -> str | None:
        """A repeater's settings, answering "get <key>" / "set <key> <value>"."""
        verb, _, rest = command.partition(" ")
        key, _, value = rest.partition(" ")
        if verb == "get":
            value = self._mock_repeater_settings.get(key)
            return f"??: {key}" if value is None else f"> {value}"
        if verb == "set" and key in self._mock_repeater_settings:
            self._mock_repeater_settings[key] = value
            return "OK - reboot to apply" if key == "radio" else "OK"
        return None

    def get_repeater_status(self, peer_name: str) -> dict | None:
        time.sleep(0.3)
        return {
            "bat": 4012,
            "tx_queue_len": 0,
            "noise_floor": -112,
            "last_rssi": -71,
            "nb_recv": 18234,
            "nb_sent": 6110,
            "airtime": 1834,
            "uptime": 3 * 86400 + 5 * 3600,
            "sent_flood": 4021,
            "sent_direct": 2089,
            "recv_flood": 15210,
            "recv_direct": 3024,
            "full_evts": 0,
            "last_snr": 7.25,
            "direct_dups": 12,
            "flood_dups": 940,
            "rx_airtime": 9120,
        }

    def get_repeater_owner_info(self, peer_name: str) -> dict | None:
        return {"version": "v1.16.0-mock", "name": peer_name, "owner_info": "mock owner"}

    def get_repeater_neighbours(self, peer_name: str) -> list[dict] | None:
        time.sleep(0.3)
        return [
            {"pubkey": "a1b2c3d4e5f6", "name": None, "secs_ago": 42, "snr": 9.5},
            {"pubkey": "13ab5c7e0011", "name": "Hilltop", "secs_ago": 610, "snr": -3.25},
            {"pubkey": "77cc0011aabb", "name": None, "secs_ago": 86400, "snr": 1.0},
        ]

    def get_repeater_access_list(self, peer_name: str) -> list[dict] | None:
        return [
            {"key": "46a5a5adaf41", "role": "admin", "name": self._settings.node_name},
            {"key": "a1b2c3d4e5f6", "role": "guest", "name": None},
        ]

    def get_repeater_login_state(self, peer_name: str) -> RepeaterLoginState | None:
        return self._repeater_sessions.get(peer_name)

    def list_logged_in_repeaters(self) -> list[str]:
        return list(self._repeater_sessions.keys())

    def get_saved_repeater_password(self, peer_name: str) -> str | None:
        return self._saved_passwords.get(peer_name)

    def delete_saved_repeater_password(self, peer_name: str) -> None:
        self._saved_passwords.pop(peer_name, None)

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
            channel_name = dm_channel_id(sender_name)
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
