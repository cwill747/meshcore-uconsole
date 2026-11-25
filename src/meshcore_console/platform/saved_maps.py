"""Named offline areas sharing one Protomaps tile per coordinate."""

import gzip
import json
import logging
import sqlite3
from contextlib import closing
from functools import lru_cache
from threading import Lock

from pmtiles.tile import Compression, TileType, zxy_to_tileid

from .offline_area import tile_ranges
from .protomaps import ATTRIBUTION, BASEMAP_MAX_ZOOM, ProtomapsReader, render_tile

logger = logging.getLogger(__name__)

DATABASE = "saved-maps.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS areas(name TEXT PRIMARY KEY, bounds TEXT, lon REAL, lat REAL,
                                 zoom INTEGER, native_zoom INTEGER);
CREATE TABLE IF NOT EXISTS tiles(id INTEGER PRIMARY KEY, data BLOB);
CREATE TABLE IF NOT EXISTS coverage(area TEXT, tile INTEGER, PRIMARY KEY(area, tile)) WITHOUT ROWID;
"""


class SavedArea:
    def __init__(self, directory, row):
        name, bounds, lon, lat, zoom, self.max_zoom = row
        self.path = directory / (name + ".area")
        self.meta = dict(
            name=name,
            selection=json.loads(bounds),
            attribution=ATTRIBUTION,
            center=f"{lon},{lat},{zoom}",
        )
        self.header = dict(
            center_lon_e7=round(lon * 1e7), center_lat_e7=round(lat * 1e7), center_zoom=zoom
        )
        self._db = (directory / DATABASE).as_uri() + "?mode=ro"
        self._lock = Lock()
        self._cached = lru_cache(maxsize=64)(self._render)

    def get_metadata(self):
        return self.meta

    def get_tile(self, zoom, x, y):
        with self._lock:
            return self._cached(zoom, x, y)

    def _render(self, zoom, x, y):
        with closing(sqlite3.connect(self._db, uri=True)) as db:
            row = db.execute(
                "SELECT data FROM coverage JOIN tiles ON id=tile WHERE area=? AND tile=?",
                (self.meta["name"], zxy_to_tileid(zoom, x, y)),
            ).fetchone()
        return render_tile(gzip.decompress(row[0])) if row and row[0] else None


def saved_areas(directory):
    path = directory / DATABASE
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return [
            SavedArea(directory, row)
            for row in db.execute(
                "SELECT name,bounds,lon,lat,zoom,native_zoom FROM areas ORDER BY name"
            )
        ]


def _tile_loader(directory, open_source, replace):
    legacy = []
    if not replace:
        for archive in directory.glob("*.pmtiles"):
            try:
                legacy.append(ProtomapsReader(archive))
            except Exception:  # Ignore unreadable standalone archives.
                logger.warning("Could not reuse archive %s", archive, exc_info=True)

    source = None
    header = None

    def load(z, x, y):
        nonlocal source, header
        data = None
        for archive in legacy:
            data = archive.reader.get(z, x, y)
            if data:
                compression = archive.header["tile_compression"]
                break
        if not data:
            if source is None:
                source = open_source()
                header = source.header()
                if header["tile_type"] != TileType.MVT or header["tile_compression"] not in (
                    Compression.GZIP,
                    Compression.NONE,
                ):
                    raise ValueError("Unsupported Protomaps vector encoding.")
            data = source.get(z, x, y) if z <= header["max_zoom"] else None
            compression = header["tile_compression"]
        if data and compression == Compression.NONE:
            data = gzip.compress(data)
        return data or None

    return load


def _coordinates(bounds, zoom):
    for z in range(min(zoom, BASEMAP_MAX_ZOOM) + 1):
        xs, ys = tile_ranges(bounds, z)
        for y in ys:
            for x in xs:
                yield z, x % (1 << z), y


def save_area(path, bounds, zoom, center, open_source, cancelled, progress, *, replace=False):
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    name = path.stem
    with closing(sqlite3.connect(directory / DATABASE)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(_SCHEMA)
        # Serialize writers before checking for cached tiles. Readers keep their last committed view.
        try:
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                raise ValueError(
                    "Another map download is running. Try again when it finishes."
                ) from exc
            raise
        if db.execute("SELECT 1 FROM areas WHERE name=?", (name,)).fetchone() and not replace:
            raise ValueError("An area with that name already exists.")
        db.execute("DELETE FROM coverage WHERE area=?", (name,))
        load = _tile_loader(directory, open_source, replace)
        native_zoom = -1
        for done, (z, x, y) in enumerate(_coordinates(bounds, zoom), 1):
            if cancelled():
                raise InterruptedError("Download cancelled.")
            tile = zxy_to_tileid(z, x, y)
            cached = (
                None
                if replace
                else db.execute("SELECT data IS NOT NULL FROM tiles WHERE id=?", (tile,)).fetchone()
            )
            if cached is None:
                data = load(z, x, y)
                db.execute("INSERT OR REPLACE INTO tiles VALUES (?,?)", (tile, data or None))
                present = bool(data)
            else:
                present = cached[0]
            if present:
                native_zoom = z
            db.execute("INSERT INTO coverage VALUES (?,?)", (name, tile))
            progress(done)
        if cancelled():
            raise InterruptedError("Download cancelled.")
        if native_zoom < 0:
            raise ValueError("No tiles in this area.")
        db.execute(
            "INSERT OR REPLACE INTO areas VALUES (?,?,?,?,?,?)",
            (name, json.dumps(bounds), *center, zoom, native_zoom),
        )
