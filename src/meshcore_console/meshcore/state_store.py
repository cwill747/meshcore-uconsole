"""Persistent state storage for messages, peers, and channels (SQLite)."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime

from meshcore_console.core.models import Channel, Message, Peer

logger = logging.getLogger(__name__)

MAX_MESSAGES = 500


class MessageStore:
    """Persistent message storage backed by SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(self, message: Message) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO messages "
            "(message_id, sender_id, body, channel_id, created_at, is_outgoing, path_len, snr, rssi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message.message_id,
                message.sender_id,
                message.body,
                message.channel_id,
                message.created_at.isoformat(),
                int(message.is_outgoing),
                message.path_len,
                message.snr,
                message.rssi,
            ),
        )
        # Prune oldest messages beyond limit
        count = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        if count > MAX_MESSAGES:
            self._conn.execute(
                "DELETE FROM messages WHERE message_id IN "
                "(SELECT message_id FROM messages ORDER BY created_at ASC LIMIT ?)",
                (count - MAX_MESSAGES,),
            )
        self._conn.commit()

    def flush_if_dirty(self) -> None:
        pass

    def get_all(self) -> list[Message]:
        rows = self._conn.execute(
            "SELECT message_id, sender_id, body, channel_id, created_at, "
            "is_outgoing, path_len, snr, rssi FROM messages ORDER BY created_at"
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    def get_for_channel(self, channel_id: str, limit: int = 50) -> list[Message]:
        rows = self._conn.execute(
            "SELECT message_id, sender_id, body, channel_id, created_at, "
            "is_outgoing, path_len, snr, rssi FROM messages "
            "WHERE channel_id = ? ORDER BY created_at",
            (channel_id,),
        ).fetchall()
        return (
            [_row_to_message(r) for r in rows[-limit:]]
            if limit > 0
            else [_row_to_message(r) for r in rows]
        )

    def remove_for_channel(self, channel_id: str) -> None:
        self._conn.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
        self._conn.commit()

    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


class PeerStore:
    """Persistent peer storage backed by SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_or_update(self, peer: Peer) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO peers "
            "(peer_id, display_name, signal_quality, public_key, last_advert_time, "
            "last_path, is_repeater, rssi, snr, latitude, longitude, location_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                peer.peer_id,
                peer.display_name,
                peer.signal_quality,
                peer.public_key,
                peer.last_advert_time.isoformat() if peer.last_advert_time else None,
                json.dumps(peer.last_path) if peer.last_path else None,
                int(peer.is_repeater),
                peer.rssi,
                peer.snr,
                peer.latitude,
                peer.longitude,
                peer.location_updated.isoformat() if peer.location_updated else None,
            ),
        )
        self._conn.commit()

    def flush_if_dirty(self) -> None:
        pass

    def get(self, name: str) -> Peer | None:
        row = self._conn.execute(
            "SELECT peer_id, display_name, signal_quality, public_key, last_advert_time, "
            "last_path, is_repeater, rssi, snr, latitude, longitude, location_updated "
            "FROM peers WHERE display_name = ?",
            (name,),
        ).fetchone()
        return _row_to_peer(row) if row else None

    def get_all(self) -> dict[str, Peer]:
        rows = self._conn.execute(
            "SELECT peer_id, display_name, signal_quality, public_key, last_advert_time, "
            "last_path, is_repeater, rssi, snr, latitude, longitude, location_updated "
            "FROM peers"
        ).fetchall()
        result: dict[str, Peer] = {}
        for row in rows:
            peer = _row_to_peer(row)
            result[peer.display_name or peer.peer_id] = peer
        return result

    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM peers").fetchone()[0]


class UIChannelStore:
    """Persistent UI channel state backed by SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_or_update(self, channel: Channel) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO channels (channel_id, display_name, unread_count, peer_name) "
            "VALUES (?, ?, ?, ?)",
            (channel.channel_id, channel.display_name, channel.unread_count, channel.peer_name),
        )
        self._conn.commit()

    def flush_if_dirty(self) -> None:
        pass

    def get(self, channel_id: str) -> Channel | None:
        row = self._conn.execute(
            "SELECT channel_id, display_name, unread_count, peer_name FROM channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return None
        return Channel(
            channel_id=row[0], display_name=row[1], unread_count=row[2], peer_name=row[3]
        )

    def get_all(self) -> dict[str, Channel]:
        rows = self._conn.execute(
            "SELECT channel_id, display_name, unread_count, peer_name FROM channels"
        ).fetchall()
        return {
            row[0]: Channel(
                channel_id=row[0], display_name=row[1], unread_count=row[2], peer_name=row[3]
            )
            for row in rows
        }

    def remove(self, channel_id: str) -> None:
        self._conn.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
        self._conn.commit()

    def increment_unread(self, channel_id: str) -> None:
        self._conn.execute(
            "UPDATE channels SET unread_count = unread_count + 1 WHERE channel_id = ?",
            (channel_id,),
        )
        self._conn.commit()

    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]


def _row_to_message(row: tuple) -> Message:
    created_at = row[4]
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    else:
        created_at = datetime.now(UTC)
    return Message(
        message_id=row[0],
        sender_id=row[1],
        body=row[2],
        channel_id=row[3],
        created_at=created_at,
        is_outgoing=bool(row[5]),
        path_len=row[6] or 0,
        snr=row[7],
        rssi=row[8],
    )


def _row_to_peer(row: tuple) -> Peer:
    last_advert = row[4]
    if isinstance(last_advert, str):
        last_advert = datetime.fromisoformat(last_advert)
    last_path = row[5]
    if isinstance(last_path, str):
        last_path = json.loads(last_path)
    else:
        last_path = []
    location_updated = row[11]
    if isinstance(location_updated, str):
        location_updated = datetime.fromisoformat(location_updated)
    return Peer(
        peer_id=row[0],
        display_name=row[1],
        signal_quality=row[2],
        public_key=row[3],
        last_advert_time=last_advert,
        last_path=last_path,
        is_repeater=bool(row[6]),
        rssi=row[7],
        snr=row[8],
        latitude=row[9],
        longitude=row[10],
        location_updated=location_updated,
    )
