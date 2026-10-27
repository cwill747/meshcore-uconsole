from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, fields

from .settings import MeshcoreSettings

logger = logging.getLogger(__name__)


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
                try:
                    defaults[field.name] = _cast(raw, field.type)
                except (TypeError, ValueError):
                    # One bad row (hand-edited, or written by another version)
                    # used to raise here and keep the app from starting at all.
                    logger.warning(
                        "settings: ignoring invalid %s=%r, using the default", field.name, raw
                    )
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
