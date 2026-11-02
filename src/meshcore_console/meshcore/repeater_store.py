"""Persistent storage for saved repeater admin passwords."""

from __future__ import annotations

import base64
import sqlite3
from datetime import UTC, datetime


class RepeaterPasswordStore:
    """Saved passwords, each bound to the full public key of its repeater.

    Not to its name: names are advertised by whoever holds a key, and a
    password saved under a name was offered to any key that later held it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_password(self, public_key: str, password: str) -> None:
        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
        self._conn.execute(
            "INSERT OR REPLACE INTO repeater_passwords (public_key, password, created_at) "
            "VALUES (?, ?, ?)",
            (public_key.lower(), encoded, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def get_password(self, public_key: str) -> str | None:
        row = self._conn.execute(
            "SELECT password FROM repeater_passwords WHERE public_key = ?",
            (public_key.lower(),),
        ).fetchone()
        if row is None:
            return None
        return base64.b64decode(row[0]).decode("utf-8")

    def delete_password(self, public_key: str) -> None:
        self._conn.execute(
            "DELETE FROM repeater_passwords WHERE public_key = ?", (public_key.lower(),)
        )
        self._conn.commit()

    def list_saved(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT public_key FROM repeater_passwords ORDER BY public_key"
        ).fetchall()
        return [r[0] for r in rows]
