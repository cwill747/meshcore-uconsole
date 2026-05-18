"""Persistent storage for saved repeater admin passwords."""

from __future__ import annotations

import base64
import sqlite3
from datetime import UTC, datetime


class RepeaterPasswordStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_password(self, peer_name: str, password: str) -> None:
        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
        self._conn.execute(
            "INSERT OR REPLACE INTO repeater_passwords (peer_name, password, created_at) "
            "VALUES (?, ?, ?)",
            (peer_name, encoded, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def get_password(self, peer_name: str) -> str | None:
        row = self._conn.execute(
            "SELECT password FROM repeater_passwords WHERE peer_name = ?",
            (peer_name,),
        ).fetchone()
        if row is None:
            return None
        return base64.b64decode(row[0]).decode("utf-8")

    def delete_password(self, peer_name: str) -> None:
        self._conn.execute("DELETE FROM repeater_passwords WHERE peer_name = ?", (peer_name,))
        self._conn.commit()

    def list_saved(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT peer_name FROM repeater_passwords ORDER BY peer_name"
        ).fetchall()
        return [r[0] for r in rows]
