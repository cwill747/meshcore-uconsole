"""Bounded on-disk cache of validated Protomaps v4 vector tiles."""

import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Lock, Thread

from meshcore_console.meshcore.settings import DEFAULT_RENDER_CACHE_MIB

from pmtiles.tile import zxy_to_tileid


MAX_TILES = 1024
logger = logging.getLogger(__name__)


class TileCache:
    def __init__(self, filename):
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


class BrowsedTiles(TileCache):
    def __init__(self):
        super().__init__("protomaps-v4.sqlite3")

    def get(self, z, x, y):
        return super().get(zxy_to_tileid(z, x, y))

    def put(self, z, x, y, data):
        key = zxy_to_tileid(z, x, y)
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
                    (MAX_TILES,),
                )
        except (sqlite3.Error, OSError):
            logger.debug("Cannot write tile cache", exc_info=True)


class RenderedTiles(TileCache):
    """Bound the committed SQLite file size, including indexes and page overhead."""

    limit_bytes = DEFAULT_RENDER_CACHE_MIB << 20
    _writer = Lock()

    def __init__(self):
        super().__init__("protomaps-rendered-v1.sqlite3")

    @classmethod
    def configure(cls, mib):
        cls.limit_bytes = max(0, mib) << 20
        cache = cls()
        if cache.path.is_file():
            Thread(target=cache._update, daemon=True, name="resize-render-cache").start()

    def get(self, key):
        return super().get(key) if self.limit_bytes else None

    def put(self, key, data):
        if len(data) <= self.limit_bytes:
            self._update(key, data)

    def _update(self, key=None, data=None):
        try:
            with self._writer:
                limit = self.limit_bytes
                if not limit:
                    self.path.unlink(missing_ok=True)
                    return
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(self.path)) as db, db:
                    if db.execute("PRAGMA auto_vacuum").fetchone()[0] != 1:
                        # Existing count-limited caches need a one-time layout migration.
                        db.execute("PRAGMA auto_vacuum=FULL")
                        db.execute("VACUUM")
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS tiles(tile INTEGER UNIQUE, data BLOB NOT NULL)"
                    )
                    db.execute("BEGIN IMMEDIATE")
                    if data is not None and len(data) <= limit:
                        db.execute("INSERT OR REPLACE INTO tiles VALUES (?,?)", (key, data))
                    page_size = db.execute("PRAGMA page_size").fetchone()[0]
                    while True:
                        pages = db.execute("PRAGMA page_count").fetchone()[0]
                        free = db.execute("PRAGMA freelist_count").fetchone()[0]
                        excess = (pages - free) * page_size - limit
                        if excess <= 0:
                            break
                        oldest = db.execute(
                            "SELECT rowid,length(data) FROM tiles ORDER BY rowid LIMIT 1024"
                        )
                        cutoff = None
                        for cutoff, size in oldest:
                            excess -= size
                            if excess <= 0:
                                break
                        oldest.close()
                        if cutoff is None:
                            break
                        db.execute("DELETE FROM tiles WHERE rowid <= ?", (cutoff,))
        except (sqlite3.Error, OSError):
            logger.debug("Cannot update rendered tile cache", exc_info=True)
