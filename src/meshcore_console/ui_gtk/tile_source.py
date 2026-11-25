"""Serve locally rendered Protomaps tiles to Shumate."""

import gi

gi.require_version("Shumate", "1.0")
from gi.repository import Gio, GLib, Shumate


class TileSource(Shumate.DataSource):
    def __init__(self, reader):
        super().__init__()
        self.reader = reader

    def do_get_tile_data_async(self, x, y, zoom, cancellable, callback, user_data):
        # Older PyGObject binds the closure data inside the native callback.
        # Newer versions expose it as a separate argument.
        def complete(src, result):
            # Shumate 1.0 paints on received-data; completion removes the tile
            # from its pending list. Emit on the UI thread before completing it.
            if not hasattr(Shumate.DataSource, "do_start_request") and not result.had_error():
                self.emit("received-data", x, y, zoom, result.tile_bytes)
            if gi.version_info < (3, 50, 0):
                callback(src, result)
            else:
                callback(src, result, user_data)

        task = Gio.Task.new(self, cancellable, complete)

        def read_archive(task, _source, _data, _cancel):
            try:
                if task.return_error_if_cancelled():
                    return
                tile = self.reader.get_tile(zoom, x, y) if self.reader else None
            except Exception as exc:
                task.return_error(
                    GLib.Error.new_literal(
                        Gio.io_error_quark(),
                        f"Cannot read offline tile: {exc}",
                        Gio.IOErrorEnum.INVALID_DATA,
                    )
                )
                return
            if tile is None:
                task.return_error(
                    GLib.Error.new_literal(
                        Gio.io_error_quark(), "Tile outside saved areas", Gio.IOErrorEnum.NOT_FOUND
                    )
                )
            else:
                task.tile_bytes = GLib.Bytes.new(tile)
                task.return_boolean(True)

        task.run_in_thread(read_archive)

    def do_get_tile_data_finish(self, result):
        result.propagate_boolean()
        return result.tile_bytes

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
