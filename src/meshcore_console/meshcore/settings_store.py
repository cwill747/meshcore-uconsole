from __future__ import annotations

import sqlite3
from dataclasses import asdict, fields

from .settings import MeshcoreSettings


class SettingsStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def load(self) -> MeshcoreSettings:
        rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        if not rows:
            return MeshcoreSettings()
        stored = {key: value for key, value in rows}
        defaults = asdict(MeshcoreSettings())
        for field in fields(MeshcoreSettings):
            if field.name in stored:
                raw = stored[field.name]
                defaults[field.name] = _cast(raw, field.type)
        return MeshcoreSettings(**defaults)

    def save(self, settings: MeshcoreSettings) -> None:
        data = asdict(settings)
        self._conn.executemany(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in data.items()],
        )
        self._conn.commit()


def _cast(raw: str, type_hint: str) -> object:
    """Cast a string value back to the expected Python type."""
    if type_hint == "bool":
        return raw in ("True", "1", "true")
    if type_hint == "int":
        return int(raw)
    if type_hint == "float":
        return float(raw)
    return raw
