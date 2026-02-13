"""Persistent state storage for messages, peers, and channels."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from meshcore_console.core.models import Channel, Message, Peer

from .paths import messages_path, peers_path, ui_channels_path

logger = logging.getLogger(__name__)

MAX_MESSAGES = 500


def _message_to_dict(msg: Message) -> dict[str, Any]:
    """Convert Message to JSON-serializable dict."""
    return {
        "message_id": msg.message_id,
        "sender_id": msg.sender_id,
        "body": msg.body,
        "channel_id": msg.channel_id,
        "created_at": msg.created_at.isoformat(),
        "is_outgoing": msg.is_outgoing,
        "path_len": msg.path_len,
        "snr": msg.snr,
        "rssi": msg.rssi,
    }


def _dict_to_message(d: dict[str, Any]) -> Message:
    """Convert dict back to Message."""
    created_at = d.get("created_at")
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    else:
        created_at = datetime.now()
    return Message(
        message_id=d.get("message_id", ""),
        sender_id=d.get("sender_id", ""),
        body=d.get("body", ""),
        channel_id=d.get("channel_id", "public"),
        created_at=created_at,
        is_outgoing=d.get("is_outgoing", False),
        path_len=d.get("path_len", 0),
        snr=d.get("snr"),
        rssi=d.get("rssi"),
    )


class MessageStore:
    """Persistent message storage."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else messages_path()
        self._messages: list[Message] = []
        logger.debug("MessageStore initialized with path: %s", self._path)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.debug("MessageStore: no existing file at %s", self._path)
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            messages = data.get("messages", [])
            if isinstance(messages, list):
                self._messages = [_dict_to_message(m) for m in messages[-MAX_MESSAGES:]]
            logger.debug("MessageStore: loaded %d messages", len(self._messages))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("MessageStore load error: %s", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"messages": [_message_to_dict(m) for m in self._messages]}
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("MessageStore save error: %s", e)
            raise

    def append(self, message: Message) -> None:
        """Add a message, maintaining max size."""
        self._messages.append(message)
        if len(self._messages) > MAX_MESSAGES:
            self._messages = self._messages[-MAX_MESSAGES:]
        self._save()

    def get_all(self) -> list[Message]:
        """Return all messages."""
        return list(self._messages)

    def get_for_channel(self, channel_id: str, limit: int = 50) -> list[Message]:
        """Return messages for a specific channel."""
        filtered = [m for m in self._messages if m.channel_id == channel_id]
        return filtered[-limit:] if limit > 0 else filtered

    def __len__(self) -> int:
        return len(self._messages)


class PeerStore:
    """Persistent peer storage."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else peers_path()
        self._peers: dict[str, Peer] = {}
        logger.debug("PeerStore initialized with path: %s", self._path)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.debug("PeerStore: no existing file at %s", self._path)
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            peers = data.get("peers", [])
            if isinstance(peers, list):
                for p in peers:
                    last_advert = p.get("last_advert_time")
                    if isinstance(last_advert, str):
                        last_advert = datetime.fromisoformat(last_advert)
                    location_updated = p.get("location_updated")
                    if isinstance(location_updated, str):
                        location_updated = datetime.fromisoformat(location_updated)
                    peer = Peer(
                        peer_id=p.get("peer_id", ""),
                        display_name=p.get("display_name", ""),
                        signal_quality=p.get("signal_quality"),
                        public_key=p.get("public_key"),
                        last_advert_time=last_advert,
                        last_path=p.get("last_path", []),
                        is_repeater=p.get("is_repeater", False),
                        rssi=p.get("rssi"),
                        snr=p.get("snr"),
                        latitude=p.get("latitude"),
                        longitude=p.get("longitude"),
                        location_updated=location_updated,
                    )
                    if peer.peer_id:
                        self._peers[peer.display_name or peer.peer_id] = peer
            logger.debug("PeerStore: loaded %d peers", len(self._peers))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("PeerStore load error: %s", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            peers_data = []
            for p in self._peers.values():
                d = asdict(p)
                # Convert datetime to ISO string for JSON
                if d.get("last_advert_time") is not None:
                    d["last_advert_time"] = d["last_advert_time"].isoformat()
                if d.get("location_updated") is not None:
                    d["location_updated"] = d["location_updated"].isoformat()
                peers_data.append(d)
            data = {"peers": peers_data}
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("PeerStore save error: %s", e)
            raise

    def add_or_update(self, peer: Peer) -> None:
        """Add or update a peer."""
        key = peer.display_name or peer.peer_id
        self._peers[key] = peer
        self._save()

    def get(self, name: str) -> Peer | None:
        """Get peer by name."""
        return self._peers.get(name)

    def get_all(self) -> dict[str, Peer]:
        """Return all peers."""
        return dict(self._peers)

    def __len__(self) -> int:
        return len(self._peers)


class UIChannelStore:
    """Persistent UI channel state (unread counts, etc.)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else ui_channels_path()
        self._channels: dict[str, Channel] = {}
        logger.debug("UIChannelStore initialized with path: %s", self._path)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.debug("UIChannelStore: no existing file at %s", self._path)
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            channels = data.get("channels", [])
            if isinstance(channels, list):
                for c in channels:
                    channel = Channel(
                        channel_id=c.get("channel_id", ""),
                        display_name=c.get("display_name", ""),
                        unread_count=c.get("unread_count", 0),
                    )
                    if channel.channel_id:
                        self._channels[channel.channel_id] = channel
            logger.debug("UIChannelStore: loaded %d channels", len(self._channels))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("UIChannelStore load error: %s", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"channels": [asdict(c) for c in self._channels.values()]}
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("UIChannelStore save error: %s", e)
            raise

    def add_or_update(self, channel: Channel) -> None:
        """Add or update a channel."""
        self._channels[channel.channel_id] = channel
        self._save()

    def get(self, channel_id: str) -> Channel | None:
        """Get channel by ID."""
        return self._channels.get(channel_id)

    def get_all(self) -> dict[str, Channel]:
        """Return all channels."""
        return dict(self._channels)

    def increment_unread(self, channel_id: str) -> None:
        """Increment unread count for a channel."""
        if channel_id in self._channels:
            self._channels[channel_id].unread_count += 1
            self._save()

    def __len__(self) -> int:
        return len(self._channels)
