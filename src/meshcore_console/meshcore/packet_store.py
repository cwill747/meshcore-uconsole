from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime

from meshcore_console.core.types import MeshEventDict

logger = logging.getLogger(__name__)

MAX_PACKETS = 1000


class PacketStore:
    """Persistent packet history backed by SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(self, packet_data: MeshEventDict) -> None:
        received_at = packet_data.get("received_at") or datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT INTO packets (received_at, data) VALUES (?, ?)",
            (received_at, json.dumps(packet_data, default=str)),
        )
        count = self._conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
        if count > MAX_PACKETS:
            self._conn.execute(
                "DELETE FROM packets WHERE id IN (SELECT id FROM packets ORDER BY id ASC LIMIT ?)",
                (count - MAX_PACKETS,),
            )
        self._conn.commit()

    def _hydrate(self, received_at: str, data: str) -> MeshEventDict:
        event = json.loads(data)
        if "received_at" not in event:
            event["received_at"] = received_at
        return event

    def get_all(self) -> list[MeshEventDict]:
        rows = self._conn.execute("SELECT received_at, data FROM packets ORDER BY id").fetchall()
        return [self._hydrate(row[0], row[1]) for row in rows]

    def get_recent(self, limit: int = 100) -> list[MeshEventDict]:
        if limit <= 0:
            return []
        rows = self._conn.execute(
            "SELECT received_at, data FROM packets ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        # Reverse so oldest is first (chronological order)
        return [self._hydrate(row[0], row[1]) for row in reversed(rows)]

    def flush_if_dirty(self) -> None:
        pass

    def clear(self) -> None:
        self._conn.execute("DELETE FROM packets")
        self._conn.commit()

    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
