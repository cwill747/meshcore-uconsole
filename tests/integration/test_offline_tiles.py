import sqlite3
from pathlib import Path

import pytest


def test_offline_source_returns_local_tile(tmp_path, monkeypatch):
    pytest.importorskip("gi")
    from gi.repository import GLib
    from meshcore_console.ui_gtk.views.map import MapView, SHUMATE_AVAILABLE

    if not SHUMATE_AVAILABLE:
        pytest.skip("libshumate unavailable")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    tiles = Path(tmp_path, "meshcore-console", "tiles")
    tiles.mkdir(parents=True)
    (tiles / "a.mbtiles").write_bytes(b"damaged archive")
    with sqlite3.connect(tiles / "b.mbtiles") as conn:
        conn.execute("CREATE TABLE images(zoom_level, tile_column, tile_row, tile_data)")
        conn.execute("CREATE VIEW tiles AS SELECT * FROM images")
        conn.execute("INSERT INTO images VALUES (2, 1, 3, ?)", (b"local tile",))
    from meshcore_console.ui_gtk.tile_source import TileSource

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    reader, _ = MapView._load_mbtiles(None)
    source = TileSource(reader, False, cache_dir=str(tmp_path / "cache"))
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


def test_cache_first_download_and_refresh(tmp_path):
    import base64
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    pytest.importorskip("gi")
    from meshcore_console.ui_gtk.views.map import SHUMATE_AVAILABLE

    if not SHUMATE_AVAILABLE:
        pytest.skip("libshumate unavailable")
    from gi.repository import GLib
    from meshcore_console.ui_gtk.tile_source import TileSource

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
    )
    requests = []
    status = 200

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.headers.get("If-None-Match"))
            self.send_response(status)
            self.send_header("ETag", '"first"')
            self.end_headers()
            if status == 200:
                self.wfile.write(png)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/{{z}}/{{x}}/{{y}}.png"

            class PartialArchive:
                def get_tile(self, zoom, x, y):
                    return png if x == 1 else None

            source = TileSource(PartialArchive(), True, url, str(tmp_path))

            def wait(start):
                loop = GLib.MainLoop()
                answers = []

                def finished(*args):
                    answers.append(args)
                    loop.quit()

                timeout = GLib.timeout_add_seconds(4, loop.quit)
                start(finished)
                loop.run()
                GLib.source_remove(timeout)
                assert answers
                return answers[0]

            def read(x=0, zoom=0):
                _, result = wait(lambda cb: source.get_tile_data_async(x, 0, zoom, None, cb))
                return source.get_tile_data_finish(result).get_data()

            def refresh():
                return wait(lambda cb: source.refresh(0, 0, 0, None, cb))

            assert read(1, 1) == png
            assert requests == []
            assert read() == png
            assert read() == png
            assert requests == [None]
            status = 304
            assert refresh() == (None, None)
            assert requests[-1] == '"first"'
            status = 503
            assert refresh()[1] is not None
            source = TileSource(None, False, url, str(tmp_path))
            assert read() == png
            assert len(requests) == 3
        finally:
            server.shutdown()
            worker.join()
