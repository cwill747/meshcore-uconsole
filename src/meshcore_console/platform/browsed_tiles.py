"""Bounded on-disk cache of validated Protomaps v4 vector tiles."""

import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from pmtiles.tile import zxy_to_tileid


MAX_TILES = 1024
logger = logging.getLogger(__name__)


class TileCache:
    def __init__(self, filename, max_tiles):
        self.max_tiles = max_tiles
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        self.path = root / "meshcore-console" / filename

    def get(self, key):
        if not self.path.is_file():
            return None
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute("SELECT data FROM tiles WHERE tile=?", (key,)).fetchone()
                return row[0] if row else None
        except (sqlite3.Error, OSError):
            logger.debug("Cannot read tile cache", exc_info=True)
            return None

    def put(self, key, data):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=0.1)) as db, db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS tiles(tile INTEGER UNIQUE, data BLOB NOT NULL)"
                )
                db.execute("INSERT OR REPLACE INTO tiles VALUES (?,?)", (key, data))
                db.execute(
                    "DELETE FROM tiles WHERE rowid <= (SELECT rowid FROM tiles "
                    "ORDER BY rowid DESC LIMIT 1 OFFSET ?)",
                    (self.max_tiles,),
                )
        except (sqlite3.Error, OSError):
            logger.debug("Cannot write tile cache", exc_info=True)


class BrowsedTiles(TileCache):
    def __init__(self):
        super().__init__("protomaps-v4.sqlite3", MAX_TILES)

    def get(self, z, x, y):
        return super().get(zxy_to_tileid(z, x, y))

    def put(self, z, x, y, data):
        super().put(zxy_to_tileid(z, x, y), data)
