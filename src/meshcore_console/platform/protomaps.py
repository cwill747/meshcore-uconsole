"""Extract Protomaps v4 regional archives and render their vector tiles offline."""

import gzip
import io
import json
import math
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from threading import Lock
from urllib.request import Request, urlopen

import mapbox_vector_tile
from PIL import Image, ImageDraw, ImageFont
from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from .offline_area import tile_ranges

BUILDS = "https://build-metadata.protomaps.dev/builds.json"
ATTRIBUTION = "Protomaps · © OpenStreetMap contributors"


def latest_build():
    with urlopen(
        Request(BUILDS, headers={"User-Agent": "meshcore-uconsole/1 (offline map extract)"}),
        timeout=20,
    ) as response:
        builds = json.load(response)
    keys = [
        b["key"]
        for b in builds
        if b.get("version", "").startswith("4.") and re.fullmatch(r"\d{8}\.pmtiles", b["key"])
    ]
    if not keys:
        raise ValueError("No compatible Protomaps build is available.")
    return "https://build.protomaps.com/" + max(keys)


def remote_reader(url, cancelled):
    @lru_cache(maxsize=48)
    def read(offset, length):
        if cancelled():
            raise InterruptedError("Download cancelled.")
        end = offset + length - 1
        req = Request(
            url,
            headers={
                "User-Agent": "meshcore-uconsole/1 (offline map extract)",
                "Range": f"bytes={offset}-{end}",
                "Accept-Encoding": "identity",
            },
        )
        with urlopen(req, timeout=20) as response:
            if response.status != 206 or not response.headers.get("Content-Range", "").startswith(
                f"bytes {offset}-{end}/"
            ):
                raise ValueError("Map server did not return the requested byte range.")
            data = response.read(length)
            if len(data) != length:
                raise IOError("Incomplete response from map server.")
            return data

    return Reader(read)


def extract(path, bounds, maxzoom, center, source, cancelled, progress, *, replace=False):
    header = source.header()
    if header["tile_type"] != TileType.MVT or header["tile_compression"] not in (
        Compression.NONE,
        Compression.GZIP,
    ):
        raise ValueError("Unsupported Protomaps vector encoding.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(dir=path.parent, suffix=".partial")
    try:
        with os.fdopen(fd, "wb") as output:
            writer = Writer(output)
            done = 0
            try:
                for z in range(maxzoom + 1):
                    xs, ys = tile_ranges(bounds, z)
                    for y in ys:
                        for x in xs:
                            if cancelled():
                                raise InterruptedError("Download cancelled.")
                            x %= 1 << z
                            data = source.get(z, x, y)
                            if data:
                                writer.write_tile(zxy_to_tileid(z, x, y), data)
                            done += 1
                            progress(done)
                if not writer.addressed_tiles:
                    raise ValueError("No tiles in this area.")
                west, north, east, south = bounds

                def latitude(y):
                    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))

                # A wrapped area cannot be described by a single conventional longitude range.
                w, e = (
                    (west * 360 - 180, east * 360 - 180) if 0 <= west < east <= 1 else (-180, 180)
                )
                header.update(
                    min_lon_e7=round(w * 1e7),
                    max_lon_e7=round(e * 1e7),
                    min_lat_e7=round(latitude(south) * 1e7),
                    max_lat_e7=round(latitude(north) * 1e7),
                    center_lon_e7=round(center[0] * 1e7),
                    center_lat_e7=round(center[1] * 1e7),
                    center_zoom=maxzoom,
                )
                writer.finalize(
                    header,
                    {
                        "name": path.stem,
                        "selection": list(bounds),
                        "attribution": ATTRIBUTION,
                        "center": f"{center[0]},{center[1]},{maxzoom}",
                    },
                )
                output.flush()
                os.fsync(output.fileno())
            finally:
                writer.tile_f.close()
        if cancelled():
            raise InterruptedError("Download cancelled.")
        if replace:
            os.replace(staging, path)
        else:
            os.link(staging, path)
    finally:
        Path(staging).unlink(missing_ok=True)


class ProtomapsReader:
    def __init__(self, path):
        self.path = path
        with path.open("rb") as f:
            self.reader = Reader(MmapSource(f))
        self.header = self.reader.header()
        if self.header["tile_type"] != TileType.MVT or self.header["tile_compression"] not in (
            Compression.GZIP,
            Compression.NONE,
        ):
            raise ValueError("Unsupported PMTiles archive")
        self.max_zoom = self.header["max_zoom"]
        self.meta = self.reader.metadata()
        self.meta.setdefault("name", path.stem)
        self.meta.setdefault("attribution", ATTRIBUTION)
        self.meta.setdefault(
            "center",
            f"{self.header['center_lon_e7'] / 1e7},{self.header['center_lat_e7'] / 1e7},{self.header['center_zoom']}",
        )
        self._render_lock = Lock()
        self._cached_tile = lru_cache(maxsize=64)(self._render)

    def get_tile(self, zoom, x, y):
        # At world zoom Shumate can request the same wrapped tile many times
        # concurrently. Decode it once and bound peak memory on the Pi.
        with self._render_lock:
            return self._cached_tile(zoom, x, y)

    def get_metadata(self):
        return self.meta

    def _render(self, zoom, x, y):
        if zoom > self.header["max_zoom"]:
            return None
        data = self.reader.get(zoom, x, y)
        if data is None:
            return None
        if self.header["tile_compression"] == Compression.GZIP:
            data = gzip.decompress(data)
        return render_tile(data)


# Local field-map style. Coordinates are already in screen orientation.
def render_tile(data):
    layers = mapbox_vector_tile.decode(data, default_options={"y_coord_down": True})
    image = Image.new("RGB", (256, 256), "#eee9da")
    draw = ImageDraw.Draw(image)
    colors = {
        "earth": "#eee9da",
        "landcover": "#bacba3",
        "landuse": "#cdd3b7",
        "water": "#85b9d5",
        "buildings": "#c4b6aa",
        "boundaries": "#9b8394",
        "roads": "#fffaf0",
        "places": "#343a34",
        "pois": "#725e49",
    }
    labels = []
    for layer, color in colors.items():
        if layer not in layers:
            continue
        scale = 256 / layers[layer]["extent"]
        for f in layers[layer]["features"]:
            geom = f["geometry"]
            kind, coords = geom["type"], geom["coordinates"]
            props = f["properties"]

            def points(line):
                return [(round(a * scale), round(b * scale)) for a, b in line]

            if kind in ("Polygon", "MultiPolygon"):
                for poly in coords if kind == "MultiPolygon" else [coords]:
                    if not poly:
                        continue
                    if len(poly) == 1:
                        draw.polygon(points(poly[0]), fill=color)
                    else:
                        mask = Image.new("1", image.size)
                        pen = ImageDraw.Draw(mask)
                        for i, ring in enumerate(poly):
                            pen.polygon(points(ring), fill=int(i == 0))
                        image.paste(color, mask=mask)
            elif kind in ("LineString", "MultiLineString"):
                major = layer == "roads" and props.get("kind") in ("highway", "major_road")
                for line in coords if kind == "MultiLineString" else [coords]:
                    xy = points(line)
                    if len(xy) < 2:
                        continue
                    draw.line(xy, fill="#d0954f" if major else color, width=3 if major else 1)
                    if major:
                        draw.line(xy, fill="#ffdb93", width=1)
                    if layer == "roads" and props.get("name") and math.dist(xy[0], xy[-1]) >= 55:
                        labels.append(
                            (
                                1,
                                0 if major else 1,
                                xy[len(xy) // 2]
                                if len(xy) > 2
                                else ((xy[0][0] + xy[-1][0]) // 2, (xy[0][1] + xy[-1][1]) // 2),
                                str(props["name"]),
                            )
                        )
            elif kind == "Point" and layer in ("places", "pois") and props.get("name"):
                labels.append(
                    (
                        2 if layer == "pois" else 0,
                        props.get("sort_rank", 0),
                        points([coords])[0],
                        str(props["name"]),
                    )
                )
    occupied = []
    font = ImageFont.load_default(size=12)
    shown = set()
    for priority, _, (x, y), name in sorted(labels):
        if name in shown or not (0 <= x < 256 and 0 <= y < 256):
            continue
        box = draw.textbbox((x, y), name, font=font, anchor="mm", stroke_width=1)
        if box[0] < 1 or box[1] < 1 or box[2] > 255 or box[3] > 255:
            continue
        if any(
            left < box[2] and right > box[0] and top < box[3] and bottom > box[1]
            for left, top, right, bottom in occupied
        ):
            continue
        if priority != 1:
            draw.ellipse((x - 2, box[3] + 1, x + 1, box[3] + 4), fill="#343a34")
        draw.text(
            (x, y),
            name,
            font=font,
            anchor="mm",
            fill="#303830",
            stroke_width=1,
            stroke_fill="#fff9ea",
        )
        shown.add(name)
        occupied.append((box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 5))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class HostedTiles:
    """Fetch visible v4 MVT tiles on demand, retaining rendered images in memory."""

    max_zoom = 15

    def __init__(self, key, on_error):
        from threading import Event

        self._key = key
        self._stopped = Event()
        self._lock = Lock()
        self._on_error = on_error
        self._cached = lru_cache(maxsize=64)(self._fetch)

    def stop(self):
        self._stopped.set()

    def get_tile(self, zoom, x, y):
        with self._lock:
            if self._stopped.is_set():
                return None
            try:
                return self._cached(zoom, x, y)
            except Exception as exc:
                if not self._stopped.is_set():
                    self.stop()
                    status = getattr(exc, "code", None)
                    message = (
                        f"Protomaps returned HTTP {status}."
                        if status
                        else "Could not load an online Protomaps tile."
                    )
                    self._on_error(self, message)
                return None

    def _fetch(self, zoom, x, y):
        from urllib.parse import urlencode
        from urllib.error import HTTPError

        query = urlencode({"key": self._key})
        request = Request(
            f"https://api.protomaps.com/tiles/v4/{zoom}/{x}/{y}.mvt?{query}",
            headers={"User-Agent": "meshcore-uconsole/1", "Accept-Encoding": "gzip"},
        )
        try:
            with urlopen(request, timeout=8) as response:
                data = response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        if data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
        return render_tile(data)
