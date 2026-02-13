"""MBTiles map source for offline tile support."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


class MBTilesReader:
    """Read tiles from an MBTiles SQLite database.

    MBTiles is a SQLite-based format for storing map tiles.
    See: https://github.com/mapbox/mbtiles-spec
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    def open(self) -> bool:
        """Open the MBTiles database."""
        if not self._path.exists():
            print(f"[MBTiles] File not found: {self._path}", file=sys.stderr)
            return False

        try:
            self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
            # Verify it has the tiles table
            cursor = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tiles'"
            )
            if cursor.fetchone() is None:
                print(
                    f"[MBTiles] Invalid MBTiles file (no tiles table): {self._path}",
                    file=sys.stderr,
                )
                self._conn.close()
                self._conn = None
                return False
            return True
        except Exception as e:
            print(f"[MBTiles] Open error: {e}", file=sys.stderr)
            return False

    def close(self) -> None:
        """Close the MBTiles database."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def get_tile(self, zoom: int, x: int, y: int) -> bytes | None:
        """Get a tile as PNG/JPEG bytes.

        MBTiles uses TMS (Tile Map Service) coordinate system where Y is flipped
        compared to XYZ (Slippy Map) convention.
        """
        if self._conn is None:
            return None

        # Convert XYZ to TMS (flip Y)
        tms_y = (1 << zoom) - 1 - y

        try:
            cursor = self._conn.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (zoom, x, tms_y),
            )
            row = cursor.fetchone()
            if row is not None:
                return row[0]
        except Exception as e:
            print(f"[MBTiles] Get tile error: {e}", file=sys.stderr)

        return None

    def get_metadata(self) -> dict[str, str]:
        """Get MBTiles metadata."""
        if self._conn is None:
            return {}

        try:
            cursor = self._conn.execute("SELECT name, value FROM metadata")
            return dict(cursor.fetchall())
        except Exception as e:
            print(f"[MBTiles] Metadata error: {e}", file=sys.stderr)
            return {}

    @property
    def is_open(self) -> bool:
        return self._conn is not None


def find_mbtiles_files() -> list[Path]:
    """Find MBTiles files in the standard location."""
    tiles_dir = Path.home() / ".local" / "share" / "meshcore-console" / "tiles"
    if not tiles_dir.exists():
        return []

    return sorted(tiles_dir.glob("*.mbtiles"))
