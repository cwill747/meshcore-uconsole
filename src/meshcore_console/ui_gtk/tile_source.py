"""Cache-first OSM tiles with optional read-only MBTiles coverage."""

import re

import gi

gi.require_version("Shumate", "1.0")
gi.require_version("Soup", "3.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Shumate, Soup

OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
# FileCache stores the ETag and last validation time, not HTTP expiry headers.
_CACHE_MAX_AGE = 7 * GLib.TIME_SPAN_DAY


class TileSource(Shumate.DataSource):
    def __init__(self, reader, online, url=OSM_URL, cache_dir=None):
        super().__init__()
        self.reader = reader
        self.online = online
        self.url = url
        # Match TileDownloader's namespace so previously browsed OSM tiles work offline.
        self.cache = Shumate.FileCache.new_full(
            100_000_000, re.sub(r"[^A-Za-z0-9]", "_", url), cache_dir
        )
        self.session = Soup.Session(
            timeout=8,
            user_agent="meshcore-uconsole (+https://github.com/cwill747/meshcore-uconsole)",
        )

    def do_get_tile_data_async(self, x, y, zoom, cancellable, callback, user_data):
        task = Gio.Task.new(
            self, cancellable, lambda src, res, _: callback(src, res, user_data), None
        )

        def fetch():
            if task.return_error_if_cancelled():
                return
            if not self.online or zoom > 19:
                task.return_error(
                    GLib.Error.new_literal(
                        Gio.io_error_quark(), "Tile unavailable", Gio.IOErrorEnum.NOT_FOUND
                    )
                )
                return
            self.download(
                x,
                y,
                zoom,
                cancellable,
                None,
                lambda tile, error: (
                    task.return_value(tile) if tile is not None else task.return_error(error)
                ),
            )

        def read_archive(task, _source, _data, _cancel):
            tile = self.reader.get_tile(zoom, x, y)
            if tile is None:
                task.return_value(None)
            else:
                task.return_value(GLib.Bytes.new(tile))

        def cached(cache, result):
            try:
                tile, etag, cached_at = cache.get_tile_finish(result)
            except GLib.Error:
                tile = etag = cached_at = None
            if task.return_error_if_cancelled():
                return
            if tile is not None:
                fresh = (
                    cached_at is not None
                    and GLib.DateTime.new_now_utc().difference(cached_at) < _CACHE_MAX_AGE
                )
                if not self.online or zoom > 19 or fresh:
                    task.return_value(tile)
                    return

                def revalidated(updated, _error):
                    if not task.return_error_if_cancelled():
                        # A 304 or failed refresh leaves the cached tile usable.
                        task.return_value(updated if updated is not None else tile)

                self.download(x, y, zoom, cancellable, etag, revalidated)
            elif self.reader is not None:

                def archive_read(_source, result):
                    try:
                        _, tile = result.propagate_value()
                    except GLib.Error as error:
                        task.return_error(error)
                        return
                    if tile:
                        task.return_value(tile)
                    else:
                        fetch()

                archive_task = Gio.Task.new(self, cancellable, archive_read)
                archive_task.run_in_thread(read_archive)
            else:
                fetch()

        self.cache.get_tile_async(x, y, zoom, cancellable, cached)

    def do_get_tile_data_finish(self, result):
        return result.propagate_value()[1]

    if hasattr(Shumate.DataSource, "do_start_request"):

        def do_start_request(self, x, y, zoom, cancellable):
            request = Shumate.DataSourceRequest.new(x, y, zoom)

            def complete(source, result):
                try:
                    tile = source.get_tile_data_finish(result)
                except GLib.Error as error:
                    request.emit_error(error)
                else:
                    request.emit_data(tile, True)

            self.get_tile_data_async(x, y, zoom, cancellable, complete)
            return request

    def refresh(self, x, y, zoom, cancellable, done):
        def cached(cache, result):
            try:
                tile, etag, _ = cache.get_tile_finish(result)
            except GLib.Error:
                tile, etag = None, None
            self.download(x, y, zoom, cancellable, etag if tile else None, done)

        self.cache.get_tile_async(x, y, zoom, cancellable, cached)

    def download(self, x, y, zoom, cancellable, etag, done):
        message = Soup.Message.new("GET", self.url.format(x=x, y=y, z=zoom))
        if etag:
            message.get_request_headers().append("If-None-Match", etag)

        def received(session, result):
            try:
                tile = session.send_and_read_finish(result)
                status = message.get_status()
                if status == 304 and etag:
                    self.cache.mark_up_to_date(x, y, zoom)
                    done(None, None)
                    return
                if status != 200:
                    raise GLib.Error.new_literal(
                        Gio.io_error_quark(),
                        f"Tile server returned {int(status)}",
                        Gio.IOErrorEnum.FAILED,
                    )
                Gdk.Texture.new_from_bytes(tile)  # Validate before replacing a working tile.
            except GLib.Error as error:
                done(None, error)
                return

            def stored(cache, result):
                try:
                    cache.store_tile_finish(result)
                except GLib.Error as error:
                    done(tile, error)
                else:
                    done(tile, None)

            tag = message.get_response_headers().get_one("ETag")
            self.cache.store_tile_async(x, y, zoom, tile, tag, cancellable, stored)

        self.session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, cancellable, received)
