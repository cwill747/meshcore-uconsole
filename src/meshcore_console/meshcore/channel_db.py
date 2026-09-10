from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass


# Default Public channel secret - shared across all MeshCore devices
PUBLIC_CHANNEL_SECRET = "8b3387e9c5cdea6ac9e5edbaa115cd72"


def normalize_channel_name(name: str) -> str:
    """Canonical on-the-wire form of a hashtag channel name: no '#', lowercase."""
    return name.strip().lstrip("#").strip().lower()


def derive_channel_secret(name: str) -> str:
    """Derive the deterministic secret for a hashtag channel.

    MeshCore hashtag channel secrets are the first 32 hex chars of
    SHA-256("#" + channel_name), with the name lowercased. E.g.
    sha256("#chicago")[:32] == "c1c289b131e5222370cbc2048445844b" (value
    verified against a real device in issue #81).

    The lowercasing is essential, not cosmetic: the secret *is* the shared key
    for the channel, so two users typing "#Chicago" and "#chicago" must derive
    the same key or they silently cannot decrypt each other's messages.
    Normalizing here means no caller can get it wrong by passing a display name
    that preserved the user's original capitalization.
    """
    clean = normalize_channel_name(name)
    return hashlib.sha256(f"#{clean}".encode()).hexdigest()[:32]


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

    def ensure_channel_secret(self, name: str) -> str:
        """Ensure a secret exists for the named hashtag channel.

        Derives the deterministic secret if no row exists (matched
        case-insensitively so an existing 'Public'/'Chicago' entry is never
        overwritten). Returns the secret in effect for the channel.
        """
        row = self._conn.execute(
            "SELECT secret FROM channel_secrets WHERE name = ? COLLATE NOCASE",
            (normalize_channel_name(name),),
        ).fetchone()
        if row is not None:
            return row[0]
        secret = derive_channel_secret(name)
        self.add_channel(normalize_channel_name(name), secret)
        return secret

    def resolve_name(self, name: str) -> str | None:
        """Return the stored name for a channel, matched case-insensitively.

        openhop_core matches ``channels_config`` entries by exact name, so senders
        must use the name as stored here ("Public"), not whatever casing the UI
        happens to display.
        """
        row = self._conn.execute(
            "SELECT name FROM channel_secrets WHERE name = ? COLLATE NOCASE",
            (normalize_channel_name(name),),
        ).fetchone()
        return row[0] if row is not None else None

    def remove_channel(self, name: str) -> None:
        self._conn.execute("DELETE FROM channel_secrets WHERE name = ? COLLATE NOCASE", (name,))
        self._conn.commit()

    def remove_derived_secret(self, name: str) -> bool:
        """Delete a channel secret only if it is the value derived from its name.

        A hashtag channel's secret can always be re-derived, so dropping it with
        the channel is harmless. A secret imported with ``radio_cli
        import-channel`` cannot be recovered, so removing the channel from the
        UI must not destroy it. Returns True if a row was deleted.
        """
        row = self._conn.execute(
            "SELECT name, secret FROM channel_secrets WHERE name = ? COLLATE NOCASE",
            (normalize_channel_name(name),),
        ).fetchone()
        if row is None or row[1] != derive_channel_secret(row[0]):
            return False
        self._conn.execute("DELETE FROM channel_secrets WHERE name = ?", (row[0],))
        self._conn.commit()
        return True

    def get_region_scope(self, name: str) -> str | None:
        """Return the region scope for a channel, or None if unset."""
        row = self._conn.execute(
            "SELECT region_scope FROM channel_secrets WHERE name = ? COLLATE NOCASE",
            (normalize_channel_name(name),),
        ).fetchone()
        return row[0] if row is not None and row[0] else None

    def set_region_scope(self, name: str, scope: str | None) -> None:
        """Set or clear the region scope for a channel.

        The channel must already have a secret row; callers ensure that with
        ``ensure_channel_secret``. Pass None to clear the scope so the channel
        inherits the app default scope again.
        """
        self._conn.execute(
            "UPDATE channel_secrets SET region_scope = ? WHERE name = ? COLLATE NOCASE",
            (scope, normalize_channel_name(name)),
        )
        self._conn.commit()

    def get_channels(self) -> list[dict[str, str]]:
        """Return channels in the format expected by openhop_core GroupTextHandler."""
        rows = self._conn.execute("SELECT name, secret FROM channel_secrets").fetchall()
        return [{"name": row[0], "secret": row[1]} for row in rows]

    def get_channel(self, name: str) -> dict[str, str] | None:
        row = self._conn.execute(
            "SELECT name, secret FROM channel_secrets WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return {"name": row[0], "secret": row[1]}
