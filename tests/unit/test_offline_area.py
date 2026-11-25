import gzip
import io

import mapbox_vector_tile
import pytest
from PIL import Image
from pmtiles.tile import Compression, TileType

from meshcore_console.platform.offline_area import archive_path, tile_ranges
from meshcore_console.platform.protomaps import ProtomapsReader, extract


class VectorSource:
    def header(self):
        return {"max_zoom": 15, "tile_type": TileType.MVT, "tile_compression": Compression.GZIP}

    def get(self, z, x, y):
        feature = {"geometry": "POLYGON ((0 0,4096 0,4096 2048,0 2048,0 0))", "properties": {}}
        return gzip.compress(mapbox_vector_tile.encode({"name": "water", "features": [feature]}))


def test_archive_zoom_stack_dateline_and_offline_render(tmp_path):
    bounds = (0.76, 0.01, 1.24, 0.24)
    path = archive_path(tmp_path, "Trail")

    def save():
        extract(path, bounds, 2, (180, 60), VectorSource(), lambda: False, lambda n: None)

    save()
    reader = ProtomapsReader(path)
    assert reader.get_metadata()["name"] == "Trail"
    for z, x in ((0, 0), (1, 0), (2, 3), (2, 0)):
        png = reader.get_tile(z, x, 0)
        image = Image.open(io.BytesIO(png))
        assert image.size == (256, 256)
        assert len(image.getcolors()) == 2
    assert reader.get_tile(2, 1, 2) is None
    with pytest.raises(FileExistsError):
        save()


def test_cancel_discards_partial_area(tmp_path):
    completed = []
    with pytest.raises(InterruptedError):
        extract(
            tmp_path / "test.pmtiles",
            (0, 0, 1, 1),
            2,
            (0, 0),
            VectorSource(),
            lambda: bool(completed),
            completed.append,
        )
    assert list(tmp_path.iterdir()) == []


def test_wrapped_world_count_is_bounded():
    xs, ys = tile_ranges((-2, -1, 3, 2), 4)
    assert len(xs) * len(ys) == 256


def test_extract_rejects_full_planet_response_before_reading(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from meshcore_console.platform import protomaps

    def server(request, timeout):
        assert request.get_header("Range") == "bytes=0-126"
        return nullcontext(SimpleNamespace(status=200))

    monkeypatch.setattr(protomaps, "urlopen", server)
    with pytest.raises(ValueError, match="byte range"):
        protomaps.remote_reader("https://example.org/planet.pmtiles", lambda: False).header()


def test_cancelled_refresh_preserves_previous_area(tmp_path):
    path = archive_path(tmp_path, "Camp")
    bounds = (0, 0, 1, 1)
    extract(path, bounds, 0, (0, 0), VectorSource(), lambda: False, lambda _: None)
    original = path.read_bytes()
    with pytest.raises(InterruptedError):
        extract(path, bounds, 1, (0, 0), VectorSource(), lambda: True, lambda _: None, replace=True)
    assert path.read_bytes() == original
    extract(path, bounds, 1, (0, 0), VectorSource(), lambda: False, lambda _: None, replace=True)
    assert ProtomapsReader(path).meta["selection"] == list(bounds)
    assert ProtomapsReader(path).header["max_zoom"] == 1


def test_hosted_tiles_use_key_once_and_stop_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    import io
    from urllib.parse import parse_qs, urlsplit
    from meshcore_console.platform import protomaps

    calls = []

    def serve(request, timeout):
        target = urlsplit(request.full_url)
        assert target.hostname == "api.protomaps.com"
        assert target.path == "/tiles/v4/2/1/1.mvt"
        assert parse_qs(target.query) == {"key": ["test&key"]}
        calls.append(target.path)
        return io.BytesIO(VectorSource().get(2, 1, 1))

    monkeypatch.setattr(protomaps, "urlopen", serve)
    hosted = protomaps.HostedTiles("test&key", lambda *_: pytest.fail("unexpected error"))
    assert hosted.get_tile(2, 1, 1).startswith(b"\x89PNG")
    assert hosted.get_tile(2, 1, 1).startswith(b"\x89PNG")
    hosted.stop()
    assert hosted.get_tile(2, 0, 0) is None
    assert len(calls) == 1


def test_saved_zoom_19_uses_native_parent_offline(tmp_path):
    from meshcore_console.platform.offline_area import ArchiveCollection

    path = archive_path(tmp_path, "Street")
    progress = []
    extract(
        path, (0, 0, 2**-19, 2**-19), 19, (0, 0), VectorSource(), lambda: False, progress.append
    )
    reader = ProtomapsReader(path)
    assert progress[-1] == 16
    assert reader.max_zoom == 15
    assert reader.header["center_zoom"] == 19
    assert ArchiveCollection([reader]).get_tile(19, 0, 0).startswith(b"\x89PNG")


def test_shared_areas_reuse_tiles_and_refresh_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from meshcore_console.platform.saved_maps import save_area, saved_areas, DATABASE
    import sqlite3

    class Source(VectorSource):
        calls = []

        def get(self, z, x, y):
            self.calls.append((z, x, y))
            return super().get(z, x, y)

    source = Source()

    def save(name, bounds, factory=lambda: source, **kwargs):
        save_area(
            tmp_path / (name + ".area"),
            bounds,
            1,
            (0, 0),
            factory,
            kwargs.pop("cancelled", lambda: False),
            lambda _: None,
            **kwargs,
        )

    west = (0, 0, 0.5, 0.5)
    both = (0, 0, 1, 0.5)
    save("west", west)
    save("both", both)
    assert source.calls == [(0, 0, 0), (1, 0, 0), (1, 1, 0)]
    save("copy", west, lambda: pytest.fail("fully cached area used network"))
    west_area = next(a for a in saved_areas(tmp_path) if a.path.stem == "west")
    assert west_area.get_tile(1, 0, 0)
    assert west_area.get_tile(1, 1, 0) is None
    with sqlite3.connect(tmp_path / DATABASE) as db:
        assert db.execute("SELECT count(*) FROM tiles").fetchone() == (3,)
    source.calls.clear()
    with pytest.raises(InterruptedError):
        save("west", west, replace=True, cancelled=lambda: bool(source.calls))
    assert len(saved_areas(tmp_path)) == 3
    with pytest.raises(ValueError, match="already exists"):
        save("west", west)
    source.calls.clear()
    save("west", west, replace=True)
    assert source.calls == [(0, 0, 0), (1, 0, 0)]


def test_download_reuses_browsed_vector_after_restart(tmp_path, monkeypatch):
    from meshcore_console.platform import protomaps
    from meshcore_console.platform.saved_maps import save_area, saved_areas

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(
        protomaps, "urlopen", lambda *_a, **_kw: io.BytesIO(VectorSource().get(0, 0, 0))
    )
    hosted = protomaps.HostedTiles("test", lambda *_: pytest.fail("online error"))
    tile = hosted.get_tile(0, 0, 0)
    assert tile
    hosted.stop()
    monkeypatch.setattr(protomaps, "urlopen", lambda *_a, **_kw: pytest.fail("network used"))
    assert (
        protomaps.HostedTiles("test", lambda *_: pytest.fail("cache error")).get_tile(0, 0, 0)
        == tile
    )

    def offline_source():
        raise ConnectionError("source offline")

    def save(replace=False):
        save_area(
            tmp_path / "Camp.area",
            (0, 0, 1, 1),
            0,
            (0, 0),
            offline_source,
            lambda: False,
            lambda _: None,
            replace=replace,
        )

    save()
    with pytest.raises(ConnectionError):
        save(replace=True)
    protomaps.BrowsedTiles().path.unlink()
    assert saved_areas(tmp_path)[0].get_tile(0, 0, 0) == tile


def test_online_cache_evicts_oldest_fetch(tmp_path, monkeypatch):
    from meshcore_console.platform import browsed_tiles

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(browsed_tiles, "MAX_TILES", 2)
    cache = browsed_tiles.BrowsedTiles()
    for x in range(3):
        cache.put(2, x, 0, bytes([x]))
    assert cache.get(2, 0, 0) is None
    assert cache.get(2, 1, 0) == bytes([1])
    assert cache.get(2, 2, 0) == bytes([2])


def test_delete_releases_only_unshared_tiles(tmp_path, monkeypatch):
    import sqlite3
    from meshcore_console.platform.saved_maps import DATABASE, save_area, delete_area, saved_areas

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for name, east in (("west", 0.5), ("both", 1)):
        save_area(
            tmp_path / (name + ".area"),
            (0, 0, east, 0.5),
            1,
            (0, 0),
            VectorSource,
            lambda: False,
            lambda _: None,
        )
    delete_area(tmp_path / "both.area")
    (west,) = saved_areas(tmp_path)
    assert west.get_tile(1, 0, 0)
    assert west.get_tile(1, 1, 0) is None
    with sqlite3.connect(tmp_path / DATABASE) as db:
        assert db.execute("SELECT count(*) FROM tiles").fetchone() == (2,)
    delete_area(west.path)
    assert not saved_areas(tmp_path)
    with sqlite3.connect(tmp_path / DATABASE) as db:
        assert db.execute("SELECT count(*) FROM tiles").fetchone() == (0,)


def test_offline_preview_draws_missing_child_from_best_available_parent():
    from meshcore_console.platform.offline_area import ArchiveCollection

    def tile_data(color):
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), color).save(buf, format="PNG")
        return buf.getvalue()

    class SparseArea:
        max_zoom = 4

        def __init__(self, tiles):
            self.tiles = tiles

        def get_tile(self, z, x, y):
            return self.tiles.get((z, x, y))

    coarse = SparseArea({(1, 1, 0): tile_data("red")})
    fine = SparseArea({(3, 4, 0): tile_data("blue")})
    source = ArchiveCollection([coarse, fine], offline_preview=True)
    assert Image.open(io.BytesIO(source.get_tile(4, 9, 1))).getpixel((120, 120)) == (0, 0, 255)
    assert source.get_tile(4, 0, 15) is None
