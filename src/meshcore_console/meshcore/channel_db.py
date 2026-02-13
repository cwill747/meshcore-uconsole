from __future__ import annotations

import sqlite3
from dataclasses import dataclass


# Default Public channel secret - shared across all MeshCore devices
PUBLIC_CHANNEL_SECRET = "8b3387e9c5cdea6ac9e5edbaa115cd72"


@dataclass
class ChannelConfig:
    name: str
    secret: str


class ChannelDatabase:
    """Persistent channel database for group message decryption (SQLite)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        # Always ensure Public channel exists
        existing = self.get_channel("Public")
        if existing is None:
            self.add_channel("Public", PUBLIC_CHANNEL_SECRET)

    def add_channel(self, name: str, secret: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO channel_secrets (name, secret) VALUES (?, ?)",
            (name, secret),
        )
        self._conn.commit()

    def remove_channel(self, name: str) -> None:
        self._conn.execute("DELETE FROM channel_secrets WHERE name = ?", (name,))
        self._conn.commit()

    def get_channels(self) -> list[dict[str, str]]:
        """Return channels in the format expected by pymc_core GroupTextHandler."""
        rows = self._conn.execute("SELECT name, secret FROM channel_secrets").fetchall()
        return [{"name": row[0], "secret": row[1]} for row in rows]

    def get_channel(self, name: str) -> dict[str, str] | None:
        row = self._conn.execute(
            "SELECT name, secret FROM channel_secrets WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return {"name": row[0], "secret": row[1]}
