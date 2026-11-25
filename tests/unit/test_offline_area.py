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


def test_hosted_tiles_use_key_once_and_stop_offline(monkeypatch):
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
