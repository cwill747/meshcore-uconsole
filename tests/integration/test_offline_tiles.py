def test_archive_source_delivers_local_tiles():
    import pytest

    pytest.importorskip("gi")
    from gi.repository import GLib
    from meshcore_console.ui_gtk.views.map import SHUMATE_AVAILABLE

    if not SHUMATE_AVAILABLE:
        pytest.skip("libshumate unavailable")
    from meshcore_console.ui_gtk.tile_source import TileSource

    class Reader:
        def get_tile(self, zoom, x, y):
            return b"local tile" if (zoom, x, y) == (2, 1, 0) else None

    source = TileSource(Reader())
    loop = GLib.MainLoop()
    received = []

    def done(source, result, _data):
        try:
            received.append(source.get_tile_data_finish(result).get_data())
        finally:
            loop.quit()

    timeout = GLib.timeout_add_seconds(5, loop.quit)
    source.get_tile_data_async(1, 0, 2, None, done, None)
    loop.run()
    GLib.source_remove(timeout)
    assert received == [b"local tile"]

    # Shumate 1.1+ consumes requests through this API when drawing the map.
    if hasattr(source, "start_request"):
        for x in (1, 2):
            request = source.start_request(x, 0, 2, None)
            request.connect("notify::completed", lambda *_: loop.quit())
            if not request.props.completed:
                timeout = GLib.timeout_add_seconds(5, loop.quit)
                loop.run()
                GLib.source_remove(timeout)
            assert request.props.completed
            if x == 1:
                assert request.props.data.get_data() == b"local tile"
            else:
                assert request.props.error is not None


def test_native_renderer_paints_offline_tile():
    import pytest

    pytest.importorskip("gi")
    from meshcore_console.ui_gtk.views.map import SHUMATE_AVAILABLE

    if not SHUMATE_AVAILABLE:
        pytest.skip("libshumate unavailable")
    _paint_offline_tile()


def _paint_offline_tile():
    import io
    import gi
    from PIL import Image

    gi.require_version("Shumate", "1.0")
    from gi.repository import Gio, GLib, Shumate
    from meshcore_console.ui_gtk.tile_source import TileSource

    png = io.BytesIO()
    Image.new("RGB", (256, 256), "green").save(png, format="PNG")

    class Reader:
        def get_tile(self, zoom, x, y):
            return png.getvalue() if (zoom, x, y) == (15, 1, 1) else None

    source = TileSource(Reader())
    renderer = Shumate.RasterRenderer.new_full(
        "offline", "Offline", "", "", 0, 19, 256, Shumate.MapProjection.MERCATOR, source
    )
    tile = Shumate.Tile.new_full(1, 1, 256, 15)
    loop = GLib.MainLoop()
    result = []

    def finished(source, res, *_):
        try:
            result.append(source.fill_tile_finish(res))
        finally:
            loop.quit()

    renderer.fill_tile_async(tile, Gio.Cancellable(), finished, None)
    timeout = GLib.timeout_source_new_seconds(5)
    timeout.set_callback(lambda *_: loop.quit())
    timeout.attach(None)
    loop.run()
    timeout.destroy()
    assert result == [True]
    assert tile.get_paintable() is not None


if __name__ == "__main__":
    _paint_offline_tile()
