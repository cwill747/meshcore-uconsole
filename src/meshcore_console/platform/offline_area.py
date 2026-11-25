"""Bounds and filenames for saved map areas."""

import math
import os
from pathlib import Path


def tile_ranges(bounds, zoom):
    """Unwrapped Web Mercator world fractions, including selections across the dateline."""
    west, north, east, south = bounds
    n = 1 << zoom
    start = math.floor(west * n)
    xs = range(start, min(start + n, math.ceil(east * n)))
    ys = range(max(0, math.floor(north * n)), min(n, math.ceil(south * n)))
    return xs, ys


def archive_path(directory, name):
    name = name.strip()
    if not name or "/" in name or "\\" in name or name.startswith(".") or len(name.encode()) > 220:
        raise ValueError("Choose a short area name without path separators or a leading dot.")
    return directory / (name + ".pmtiles")


def tiles_directory() -> Path:
    return (
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        / "meshcore-console"
        / "tiles"
    )


class ArchiveCollection:
    def __init__(self, readers):
        self.readers = readers

    def get_tile(self, zoom, x, y):
        for reader in self.readers:
            levels = max(0, zoom - reader.max_zoom)
            data = reader.get_tile(zoom - levels, x >> levels, y >> levels)
            if levels and data is not None:
                from io import BytesIO
                from PIL import Image

                scale = 1 << levels
                with Image.open(BytesIO(data)) as parent:
                    size = parent.width / scale
                    left, top = (x % scale) * size, (y % scale) * size
                    child = parent.resize(
                        parent.size,
                        Image.Resampling.BILINEAR,
                        box=(left, top, left + size, top + size),
                    )
                    output = BytesIO()
                    child.save(output, format="PNG")
                    return output.getvalue()
            if data is not None:
                return data
        return None
