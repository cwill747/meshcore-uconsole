"""Bounds and filenames for saved map areas."""

import math


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
