"""Bounded on-disk cache of validated Protomaps v4 vector tiles."""

import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from pmtiles.tile import zxy_to_tileid


MAX_TILES = 1024
logger = logging.getLogger(__name__)


class BrowsedTiles:
    def __init__(self):
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        self.path = root / "meshcore-console" / "protomaps-v4.sqlite3"

    def get(self, z, x, y):
        if not self.path.is_file():
            return None
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute(
                    "SELECT data FROM tiles WHERE tile=?", (zxy_to_tileid(z, x, y),)
                ).fetchone()
                return row[0] if row else None
        except (sqlite3.Error, OSError):
            logger.debug("Cannot read online tile cache", exc_info=True)
            return None

    def put(self, z, x, y, data):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS tiles(tile INTEGER UNIQUE, data BLOB NOT NULL)"
                )
                db.execute(
                    "INSERT OR REPLACE INTO tiles VALUES (?,?)", (zxy_to_tileid(z, x, y), data)
                )
                db.execute(
                    "DELETE FROM tiles WHERE rowid <= (SELECT rowid FROM tiles "
                    "ORDER BY rowid DESC LIMIT 1 OFFSET ?)",
                    (MAX_TILES,),
                )
        except (sqlite3.Error, OSError):
            logger.debug("Cannot write online tile cache", exc_info=True)
