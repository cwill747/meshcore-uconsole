from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Callable
from uuid import uuid4

from meshcore_console.core.enums import EventType, PayloadType
from meshcore_console.core.models import Channel, DeviceStatus, Message, Peer
from meshcore_console.core.radio import rssi_to_signal_percent
from meshcore_console.core.services import MeshcoreService
from meshcore_console.core.types import MeshEventDict, SendResultDict
from meshcore_console.meshcore.channel_db import ChannelDatabase
from meshcore_console.meshcore.config import runtime_config_from_settings
from meshcore_console.meshcore.db import open_db
from meshcore_console.meshcore.logging_setup import install_radio_error_handler
from meshcore_console.meshcore.packet_codec import repair_utf8
from meshcore_console.meshcore.packet_store import PacketStore
from meshcore_console.meshcore.session import PyMCCoreSession
from meshcore_console.meshcore.settings import (
    MeshcoreSettings,
    apply_hardware_preset,
    apply_preset,
)
from meshcore_console.meshcore.settings_store import SettingsStore
from meshcore_console.meshcore.state_store import MessageStore, PeerStore, UIChannelStore
from meshcore_console.platform.gps import GpsProvider, create_gps_provider

logger = logging.getLogger(__name__)


class MeshcoreClient(MeshcoreService):
    """pyMC_core-backed adapter for the UI layer."""

    def __init__(
        self,
        node_name: str = "uconsole-node",
        session: PyMCCoreSession | None = None,
        *,
        require_pymc: bool = True,
        settings_store: SettingsStore | None = None,
        packet_store: PacketStore | None = None,
        message_store: MessageStore | None = None,
        peer_store: PeerStore | None = None,
        channel_store: UIChannelStore | None = None,
        gps_provider: GpsProvider | None = None,
    ) -> None:
        self._connected = False
        self._event_notify: Callable[[], None] | None = None
        self._event_buffer: list[MeshEventDict] = []
        self._event_history: list[MeshEventDict] = []
        # Cross-batch queues for enriching packet events with handler data.
        # The raw packet callback fires before handler decryption, so these
        # hold packet data dicts until the corresponding handler event arrives.
        self._unenriched_grp: deque[dict] = deque(maxlen=20)
        self._unenriched_txt: deque[dict] = deque(maxlen=20)
        self._db = open_db()
        self._settings_store = settings_store or SettingsStore(self._db)
        self._packet_store = packet_store or PacketStore(self._db)
        self._message_store = message_store or MessageStore(self._db)
        self._peer_store = peer_store or PeerStore(self._db)
        self._channel_store = channel_store or UIChannelStore(self._db)
        self._gps_provider = gps_provider or create_gps_provider()
        # Load persisted state
        self._messages: list[Message] = self._message_store.get_all()
        self._channels: dict[str, Channel] = self._channel_store.get_all()
        self._peers: dict[str, Peer] = self._peer_store.get_all()
        self._sync_channel_secrets_to_ui()
        self._settings = self._settings_store.load()
        if node_name != "uconsole-node":
            self._settings.node_name = node_name
        self._session = session if session is not None else self._new_session()
        self._config = runtime_config_from_settings(self._settings)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

        if require_pymc:
            try:
                import pymc_core  # noqa: F401

                self._pymc_available = True
            except ImportError:
                self._pymc_available = False
        else:
            self._pymc_available = True

        self._radio_error_handler = install_radio_error_handler(self._on_radio_error)

    def _sync_channel_secrets_to_ui(self) -> None:
        """Ensure every channel secret has a corresponding UI channel entry."""
        channel_db = ChannelDatabase(self._db)
        for row in channel_db.get_channels():
            original_name = row["name"]  # Preserve original case for pyMC_core
            channel_id = original_name.lower()
            if channel_id not in self._channels:
                channel = Channel(
                    channel_id=channel_id,
                    display_name=f"#{original_name}",
                    unread_count=0,
                )
                self._channels[channel_id] = channel
                self._channel_store.add_or_update(channel)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Start a persistent event loop in a background thread if needed."""
        if self._loop is not None and self._loop.is_running():
            return self._loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True, name="meshcore-aio")
        thread.start()
        self._loop = loop
        self._loop_thread = thread
        return loop

    def _run_async(self, coro: object, *, timeout: float | None = None) -> object:
        """Submit a coroutine to the persistent loop and block for its result."""
        loop = self._ensure_loop()
        if timeout is not None:
            coro = asyncio.wait_for(coro, timeout=timeout)  # type: ignore[arg-type]
        future = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]
        return future.result()

    def _shutdown_loop(self) -> None:
        """Stop the persistent event loop and join the thread."""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=3.0)
        if self._loop is not None:
            self._loop.close()
        self._loop = None
        self._loop_thread = None

    def connect(self) -> None:
        if not self._pymc_available:
            raise RuntimeError("pyMC_core is not installed. Run in mock mode or install pyMC_core.")
        runtime_connected = bool(self._session.status().get("connected"))
        if self._connected and runtime_connected:
            return
        if self._connected and not runtime_connected:
            self._connected = False
        try:
            self._run_async(self._session.start(), timeout=8.0)
        except SystemExit as exc:
            # pyMC_core calls sys.exit() on fatal GPIO errors.  Convert to
            # RuntimeError so the UI can show the failure instead of crashing.
            self._session = self._new_session()
            self._connected = False
            raise RuntimeError(f"Radio hardware init failed (exit code {exc.code})") from exc
        except Exception:
            # Recover from partial startup by creating a clean session for next attempt.
            self._session = self._new_session()
            self._connected = False
            raise
        self._connected = True
        self._session.set_telemetry_data_fn(self._get_local_telemetry)
        self._seed_contact_book()
        self._gps_provider.start()
        self._append_event(
            {"type": EventType.SESSION_CONNECTED, "data": {"node_name": self._settings.node_name}}
        )

    def disconnect(self) -> None:
        runtime_connected = bool(self._session.status().get("connected"))
        if self._connected or runtime_connected:
            try:
                self._run_async(self._session.stop(), timeout=10.0)
            except (TimeoutError, RuntimeError):
                pass
            finally:
                # Always rotate to a fresh session after disconnect to avoid stale IRQ state.
                self._session = self._new_session()
                self._shutdown_loop()
        self._connected = False
        self._gps_provider.stop()
        self._append_event(
            {
                "type": EventType.SESSION_DISCONNECTED,
                "data": {"node_name": self._settings.node_name},
            }
        )

    def get_status(self) -> DeviceStatus:
        runtime = self._session.status()
        return DeviceStatus(
            node_id=runtime["node_name"],
            connected=self._connected,
            rssi=None,
            battery_percent=None,
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
            self._channel_store.add_or_update(channel)
        return sorted(self._channels.values(), key=lambda c: c.display_name.lower())

    def ensure_channel(self, channel_id: str, display_name: str | None = None) -> Channel:
        """Ensure a channel exists, creating it if necessary."""
        is_group = (
            channel_id == "public"
            or channel_id.startswith("#")
            or (display_name is not None and display_name.startswith("#"))
        )
        # DM channel IDs are always lowercase for consistency
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
        self._channel_store.add_or_update(channel)
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
        self._channel_store.remove(channel_id)
        self._message_store.remove_for_channel(channel_id)
        return True

    def mark_channel_read(self, channel_id: str) -> None:
        if channel_id in self._channels:
            self._channels[channel_id].unread_count = 0
            self._channel_store.reset_unread(channel_id)

    def send_message(self, peer_id: str, body: str) -> Message:
        if not self._connected:
            self.connect()

        # Look up existing channel to determine kind; fall back to string heuristic
        # for channels not yet in the store (e.g. first DM send).
        existing = self._channels.get(peer_id.lower()) or self._channels.get(peer_id)
        if existing is not None:
            is_group = existing.kind == "group"
        else:
            is_group = peer_id == "public" or peer_id.startswith("#")
        # DM channel IDs are always lowercase to match _process_message_event
        channel_id = peer_id if is_group else peer_id.lower()
        if channel_id not in self._channels:
            display = f"#{channel_id}" if is_group else peer_id
            channel = Channel(
                channel_id=channel_id,
                display_name=display,
                peer_name=peer_id if not is_group else None,
                kind="group" if is_group else "dm",
            )
            self._channels[channel_id] = channel
            self._channel_store.add_or_update(channel)

        if is_group:
            # Resolve the original-case channel name for pyMC_core.
            # display_name is "#ChannelName" — strip the "#" prefix.
            channel = self._channels[channel_id]
            channel_name = channel.display_name.lstrip("#")
            self._run_async(self._session.send_group_text(channel_name=channel_name, message=body))
        else:
            # Use the original-case peer name from the channel so pyMC_core
            # can find the contact in the contact book (case-sensitive lookup).
            channel = self._channels.get(channel_id)
            resolved_name = (channel.peer_name or channel.display_name) if channel else peer_id
            self._run_async(self._session.send_text(peer_name=resolved_name, message=body))
        message = Message(
            message_id=str(uuid4()),
            sender_id=self._settings.node_name,
            body=body,
            channel_id=channel_id,
            created_at=datetime.now(UTC),
            is_outgoing=True,
        )
        self._messages.append(message)
        self._message_store.append(message)
        self._append_event(
            {
                "type": EventType.MESSAGE_SENT,
                "data": {
                    "peer_id": peer_id,
                    "channel_id": channel_id,
                    "body": body,
                    "at": message.created_at.isoformat(),
                },
            }
        )
        return message

    def send_advert(self, name: str | None = None, *, route_type: str = "flood") -> SendResultDict:
        if not self._connected:
            self.connect()
        result = self._run_async(self._session.send_advert(name=name, route_type=route_type))
        self._append_event(
            {
                "type": EventType.ADVERT_SENT,
                "data": {
                    "name": name or self._settings.node_name,
                    "route_type": route_type,
                    "success": bool(result.get("success")),
                    "tx_metadata": result.get("tx_metadata"),
                },
            }
        )
        return result

    def poll_events(self, limit: int = 50) -> list[MeshEventDict]:
        events: list[MeshEventDict] = []
        try:
            drained = self._session.drain_events(max_items=limit)
            if isinstance(drained, list):
                events.extend(drained)
        except (RuntimeError, OSError) as exc:
            logger.debug("drain_events error: %s", exc)
        if self._event_buffer:
            events.extend(self._event_buffer)
            self._event_buffer.clear()

        # Enrich packet events with sender names before processing/persisting
        self._enrich_sender_names(events)

        for event in events:
            self._append_history(event)
            self._process_event_for_peers(event)
            # Persist packet events to state storage
            if event.get("type") in (EventType.PACKET, EventType.RAW_PACKET):
                try:
                    self._packet_store.append(event)
                except OSError as exc:
                    # Log but don't let storage failures break event processing
                    logger.warning("packet_store error: %s: %s", type(exc).__name__, exc)

        if len(events) > limit:
            return events[-limit:]
        return events

    def _build_peer_lookup(self) -> dict[str, str]:
        """Build a reverse lookup from peer_id/pubkey to display_name."""
        lookup: dict[str, str] = {}
        for peer in self._peers.values():
            if peer.peer_id:
                lookup[peer.peer_id] = peer.display_name
            if peer.public_key:
                lookup[peer.public_key] = peer.display_name
                # Also index by truncated key (sender_id is often first 16 hex chars)
                if len(peer.public_key) > 16:
                    lookup[peer.public_key[:16]] = peer.display_name
        return lookup

    def _enrich_sender_names(self, events: list[MeshEventDict]) -> None:
        """Best-effort enrichment of packet events for the analyzer display.

        pymc_core's raw packet callback fires *before* handler processing, so
        ``packet`` events often lack sender_name (and GRP_TXT/TXT_MSG lack
        channel_name / payload_text).  We try two strategies:

        1. Peer-registry lookup — matches sender_id / sender_pubkey to a
           known peer display name.
        2. Handler-event correlation — when a handler event
           (mesh.channel.message.new / mesh.message.new) arrives (possibly in
           a later poll batch), we propagate its fields back to the packet
           event.  The unenriched queues persist across batches so the
           correlation works even when the handler event is drained separately.

        This is best-effort for display only — message routing uses handler
        events directly (see _process_event_for_peers).
        """
        peer_lookup = self._build_peer_lookup()

        for event in events:
            event_type = event.get("type", "")
            data = event.get("data")
            if not isinstance(data, dict):
                continue

            if event_type in (EventType.PACKET, EventType.RAW_PACKET):
                if not data.get("sender_name"):
                    resolved = self._resolve_sender_from_peers(data, peer_lookup)
                    if resolved:
                        data["sender_name"] = resolved

                # Only queue "packet" events (not "raw_packet") for handler
                # correlation — both are emitted per packet, but we only need
                # one enriched copy.
                if event_type == EventType.PACKET:
                    payload_type = data.get("payload_type_name", "")
                    if payload_type == PayloadType.GRP_TXT and not data.get("channel_name"):
                        self._unenriched_grp.append(data)
                    elif payload_type == PayloadType.TXT_MSG and not data.get("sender_name"):
                        self._unenriched_txt.append(data)

            elif event_type == EventType.MESH_CHANNEL_MESSAGE_NEW:
                if self._unenriched_grp:
                    target = self._unenriched_grp.popleft()
                    updates: dict[str, str] = {}
                    sender = data.get("sender_name") or data.get("peer_name")
                    if sender and not target.get("sender_name"):
                        target["sender_name"] = repair_utf8(str(sender))
                        updates["sender_name"] = target["sender_name"]
                    channel = data.get("channel_name")
                    if channel:
                        target["channel_name"] = str(channel)
                        updates["channel_name"] = target["channel_name"]
                    msg_text = data.get("message_text")
                    if msg_text and not target.get("payload_text"):
                        target["payload_text"] = str(msg_text)
                        updates["payload_text"] = target["payload_text"]
                    if updates:
                        packet_hash = target.get("packet_hash")
                        if packet_hash:
                            self._packet_store.update_by_hash(packet_hash, updates)

            elif event_type == EventType.MESH_MESSAGE_NEW:
                sender = data.get("sender_name") or data.get("peer_name")
                if sender and self._unenriched_txt:
                    target = self._unenriched_txt.popleft()
                    target["sender_name"] = repair_utf8(str(sender))
                    packet_hash = target.get("packet_hash")
                    if packet_hash:
                        self._packet_store.update_by_hash(
                            packet_hash, {"sender_name": target["sender_name"]}
                        )

    def _enrich_stored_sender_names(self, events: list[MeshEventDict]) -> None:
        """Enrich stored packet events with sender names from the peer registry."""
        peer_lookup = self._build_peer_lookup()
        for event in events:
            data = event.get("data")
            if isinstance(data, dict) and not data.get("sender_name"):
                resolved = self._resolve_sender_from_peers(data, peer_lookup)
                if resolved:
                    data["sender_name"] = resolved

    @staticmethod
    def _resolve_sender_from_peers(data: dict, peer_lookup: dict[str, str]) -> str | None:
        """Try to resolve sender_name from peer lookup using sender_id or sender_pubkey."""
        sender_id = data.get("sender_id")
        if sender_id and sender_id in peer_lookup:
            return peer_lookup[sender_id]
        sender_pubkey = data.get("sender_pubkey")
        if sender_pubkey and sender_pubkey in peer_lookup:
            return peer_lookup[sender_pubkey]
        return None

    def _process_event_for_peers(self, event: MeshEventDict) -> None:
        """Extract peer info from events and update the peers list."""
        event_type = event.get("type", "")
        data = event.get("data")
        if not isinstance(data, dict):
            return

        # raw_packet events are for the analyzer only — skip peer/message processing
        # to avoid double-processing (each packet produces both "packet" and "raw_packet").
        if event_type == EventType.RAW_PACKET:
            return

        payload_type_name = data.get("payload_type_name", "")

        # Check for ADVERT packets to add new peers
        if payload_type_name == PayloadType.ADVERT or event_type in (
            EventType.CONTACT_RECEIVED,
            EventType.ADVERT_RECEIVED,
            EventType.MESH_CONTACT_NEW,
        ):
            self._process_advert_event(data)

        # Check for incoming messages.
        # For encrypted types (GRP_TXT, TXT_MSG) the "packet" event arrives
        # before decryption — it has no usable text or channel info.  Only the
        # handler events (mesh.channel.message.new / mesh.message.new) carry
        # the decrypted content, so we route messages exclusively from those.
        if event_type in (EventType.MESH_CHANNEL_MESSAGE_NEW, EventType.MESH_MESSAGE_NEW):
            self._process_message_event(data, event_type=event_type)

    def _process_advert_event(self, data: MeshEventDict) -> None:
        """Process an advert event and update or create peer."""
        peer_name = (
            data.get("sender_name")
            or data.get("advert_name")
            or data.get("peer_name")
            or data.get("name")
        )
        if not peer_name:
            return
        peer_name = repair_utf8(str(peer_name))

        peer_id = data.get("sender_id") or data.get("peer_id")
        public_key = data.get("sender_pubkey")

        # Skip our own advert reflected back through a repeater.
        self_pubkey = self.get_self_public_key()
        if self_pubkey and public_key and self_pubkey == public_key:
            return

        path_hops = data.get("path_hops", [])
        rssi_raw = data.get("rssi")
        snr_raw = data.get("snr")

        # Convert to typed values
        rssi: int | None = int(rssi_raw) if rssi_raw is not None else None
        snr: float | None = float(snr_raw) if snr_raw is not None else None

        # Extract GPS coordinates from ADVERT
        advert_lat_raw = data.get("advert_lat")
        advert_lon_raw = data.get("advert_lon")
        advert_lat: float | None = float(advert_lat_raw) if advert_lat_raw is not None else None
        advert_lon: float | None = float(advert_lon_raw) if advert_lon_raw is not None else None
        has_location = (
            advert_lat is not None
            and advert_lon is not None
            and (advert_lat != 0.0 or advert_lon != 0.0)
        )

        signal = rssi_to_signal_percent(rssi) if rssi is not None else None

        # Determine repeater status from advert_type (lower nibble of ADVERT flags byte).
        # ADV_TYPE_REPEATER = 2 per pyMC_core.
        advert_type = data.get("advert_type")
        is_repeater = int(advert_type) == 2 if advert_type is not None else False

        if peer_name in self._peers:
            self._update_existing_peer(
                peer_name,
                signal,
                path_hops,
                rssi,
                snr,
                public_key,
                has_location,
                advert_lat,
                advert_lon,
                is_repeater,
            )
        else:
            self._create_new_peer(
                peer_name,
                peer_id,
                signal,
                public_key,
                path_hops,
                is_repeater,
                rssi,
                snr,
                has_location,
                advert_lat,
                advert_lon,
            )

    def _update_existing_peer(
        self,
        peer_name: str,
        signal: int | None,
        path_hops: list[str],
        rssi: int | None,
        snr: float | None,
        public_key: str | None,
        has_location: bool,
        advert_lat: float | None,
        advert_lon: float | None,
        is_repeater: bool = False,
    ) -> None:
        """Update an existing peer with new advert data."""
        existing = self._peers[peer_name]
        existing.signal_quality = signal if signal is not None else existing.signal_quality
        existing.last_advert_time = datetime.now(UTC)
        existing.last_path = path_hops if path_hops else existing.last_path
        existing.rssi = rssi if rssi is not None else existing.rssi
        existing.snr = snr if snr is not None else existing.snr
        existing.is_repeater = is_repeater
        if public_key:
            existing.public_key = public_key
            self._sync_contact_to_book(peer_name, public_key)
        if has_location and advert_lat is not None and advert_lon is not None:
            existing.latitude = advert_lat
            existing.longitude = advert_lon
            existing.location_updated = datetime.now(UTC)
        self._peer_store.add_or_update(existing)

    def _create_new_peer(
        self,
        peer_name: str,
        peer_id: str | None,
        signal: int | None,
        public_key: str | None,
        path_hops: list[str],
        is_repeater: bool,
        rssi: int | None,
        snr: float | None,
        has_location: bool,
        advert_lat: float | None,
        advert_lon: float | None,
    ) -> None:
        """Create a new peer from advert data."""
        peer = Peer(
            peer_id=peer_id or peer_name,
            display_name=peer_name,
            signal_quality=signal,
            public_key=public_key,
            last_advert_time=datetime.now(UTC),
            last_path=path_hops,
            is_repeater=is_repeater,
            rssi=rssi,
            snr=snr,
            latitude=advert_lat if has_location else None,
            longitude=advert_lon if has_location else None,
            location_updated=datetime.now(UTC) if has_location else None,
        )
        self._peers[peer_name] = peer
        self._peer_store.add_or_update(peer)
        if public_key:
            self._sync_contact_to_book(peer_name, public_key)

    def _process_message_event(self, data: MeshEventDict, event_type: str = "") -> None:
        """Process an incoming message event."""
        sender_name = repair_utf8(
            str(data.get("sender_name") or data.get("peer_name") or "Unknown")
        )
        message_text = (
            data.get("payload_text") or data.get("message_text") or data.get("text") or ""
        )
        if not message_text:
            return

        # Direct messages (TXT_MSG) have no channel_name — route to a per-contact channel.
        # Detect via: payload_type_name, EventService event type, or absence of channel_name.
        payload_type_name = data.get("payload_type_name", "")
        is_direct = (
            payload_type_name == PayloadType.TXT_MSG
            or event_type == EventType.MESH_MESSAGE_NEW
            or not data.get("channel_name")
        )
        if is_direct:
            raw_channel = sender_name
        else:
            raw_channel = data["channel_name"]
        channel_name = raw_channel.lower()
        # Preserve the original-case sender name so we can resolve contacts later.
        peer_display_name = sender_name if is_direct else None

        # Deduplicate by message_id — handles radio retransmissions of the
        # same packet (pyMC_core derives a deterministic id from the decrypted
        # timestamp + content hash, so copies of the same packet share an id).
        msg_id = data.get("message_id") or str(uuid4())
        existing_ids = {m.message_id for m in self._messages[-100:]}
        if msg_id in existing_ids:
            return

        snr = data.get("snr")
        rssi = data.get("rssi")
        path_len = data.get("path_len") or 0
        path_hops = data.get("path_hops", [])
        message = Message(
            message_id=msg_id,
            sender_id=sender_name,
            body=message_text,
            channel_id=channel_name,
            created_at=datetime.now(UTC),
            is_outgoing=False,
            path_len=int(path_len) if path_len else 0,
            path_hops=list(path_hops) if path_hops else [],
            snr=float(snr) if snr is not None else None,
            rssi=int(rssi) if rssi is not None else None,
        )
        self._messages.append(message)
        self._message_store.append(message)

        # Ensure channel exists
        if channel_name not in self._channels:
            display = sender_name if is_direct else f"#{channel_name}"
            channel = Channel(
                channel_id=channel_name,
                display_name=display,
                unread_count=1,
                peer_name=peer_display_name,
                kind="dm" if is_direct else "group",
            )
            self._channels[channel_name] = channel
            self._channel_store.add_or_update(channel)
        else:
            self._channels[channel_name].unread_count += 1
            self._channel_store.add_or_update(self._channels[channel_name])

    def list_recent_events(self, limit: int = 50) -> list[MeshEventDict]:
        if limit <= 0:
            return []
        return self._event_history[-limit:]

    def list_stored_packets(self, limit: int = 100) -> list[MeshEventDict]:
        """Return packets from persistent storage."""
        packets = self._packet_store.get_recent(limit)
        self._enrich_stored_sender_names(packets)
        return packets

    def flush_stores(self) -> None:
        """Flush any dirty stores to disk."""
        self._packet_store.flush_if_dirty()
        self._message_store.flush_if_dirty()
        self._peer_store.flush_if_dirty()
        self._channel_store.flush_if_dirty()

    def get_stored_packet_count(self) -> int:
        """Return the number of packets in persistent storage."""
        return len(self._packet_store)

    def get_settings(self) -> MeshcoreSettings:
        return self._settings.clone()

    def update_settings(self, settings: MeshcoreSettings) -> None:
        updated = settings.clone()
        if updated.hardware_preset != "custom":
            updated = apply_hardware_preset(updated, updated.hardware_preset)
        if updated.radio_preset != "custom":
            updated = apply_preset(updated, updated.radio_preset)

        self._settings = updated
        self._settings_store.save(updated)
        self._config = runtime_config_from_settings(self._settings)

        # Prepare a fresh session so the next connect() picks up new config,
        # but do NOT restart the radio here — the user must restart the app
        # for radio-parameter changes to take effect.
        if not self._connected:
            self._session = self._new_session()

        self._append_event(
            {"type": EventType.SETTINGS_UPDATED, "data": {"node_name": updated.node_name}}
        )

    def get_device_location(self) -> tuple[float, float] | None:
        return self._gps_provider.get_location()

    def _on_radio_error(self, message: str) -> None:
        """Callback from RadioErrorHandler — emit as a UI event."""
        self._append_event({"type": EventType.RADIO_ERROR, "data": {"message": message}})

    def is_mock_mode(self) -> bool:
        return False

    def cycle_mock_gps(self) -> bool:
        """Cycle to next mock GPS position. Returns True if successful."""
        return False

    def poll_gps(self) -> bool:
        """Poll GPS for new data. Call periodically from UI."""
        return self._gps_provider.poll()

    def get_gps_error(self) -> str | None:
        """Get the last GPS error message, if any."""
        return self._gps_provider.get_last_error()

    def has_gps_fix(self) -> bool:
        """Return True if GPS has acquired a satellite fix."""
        return self._gps_provider.has_fix()

    def set_favorite(self, peer_id: str, favorite: bool) -> None:
        """Toggle the favorite flag on a peer."""
        for peer in self._peers.values():
            if peer.peer_id == peer_id:
                peer.is_favorite = favorite
                self._peer_store.set_favorite(peer_id, favorite)
                return

    def get_self_public_key(self) -> str | None:
        """Return this node's public key as a hex string, or None if unavailable."""
        return self._session.get_public_key()

    def request_telemetry(self, peer_name: str) -> dict:
        """Request telemetry data from a remote peer."""
        if not self._connected:
            self.connect()
        result = self._run_async(
            self._session.send_telemetry_request(peer_name, timeout=10.0),
            timeout=15.0,
        )
        self._append_event(
            {
                "type": EventType.TELEMETRY_RECEIVED,
                "data": {"peer_name": peer_name, "telemetry": result},
            }
        )
        return result  # type: ignore[return-value]

    def _get_local_telemetry(self) -> dict:
        """Provide local telemetry data for inbound requests."""
        loc = self._gps_provider.get_location()
        return {
            "allow": self._settings.allow_telemetry,
            "lat": loc[0] if loc else None,
            "lon": loc[1] if loc else None,
        }

    def _seed_contact_book(self) -> None:
        """Populate the session's contact book with known peers that have public keys."""
        book = self._session.contact_book
        for peer in self._peers.values():
            if peer.public_key:
                book.add_contact({"name": peer.display_name, "public_key": peer.public_key})

    def _sync_contact_to_book(self, name: str, public_key: str) -> None:
        """Add or update a single contact in the session's contact book."""
        if self._connected:
            self._session.contact_book.add_contact({"name": name, "public_key": public_key})

    def set_event_notify(self, notify_fn: Callable[[], None]) -> None:
        self._event_notify = notify_fn
        self._session.set_event_notify(notify_fn)

    def _append_event(self, event: MeshEventDict) -> None:
        self._event_buffer.append(event)
        self._append_history(event)
        if self._event_notify is not None:
            try:
                self._event_notify()
            except Exception:  # noqa: BLE001
                pass

    def _append_history(self, event: MeshEventDict) -> None:
        self._event_history.append(event)
        if len(self._event_history) > 500:
            self._event_history = self._event_history[-500:]

    def _new_session(self) -> PyMCCoreSession:
        runtime = runtime_config_from_settings(self._settings)
        session = PyMCCoreSession(runtime)
        if self._event_notify is not None:
            session.set_event_notify(self._event_notify)
        return session
