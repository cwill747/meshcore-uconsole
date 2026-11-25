import pytest


def test_archive_source_delivers_local_tiles():
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
