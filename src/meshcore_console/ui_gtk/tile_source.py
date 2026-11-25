"""Serve locally rendered Protomaps tiles to Shumate."""

import gi

gi.require_version("Shumate", "1.0")
from gi.repository import Gio, GLib, Shumate


class TileSource(Shumate.DataSource):
    def __init__(self, reader):
        super().__init__()
        self.reader = reader

    def do_get_tile_data_async(self, x, y, zoom, cancellable, callback, user_data):
        task = Gio.Task.new(
            self, cancellable, lambda src, res, _: callback(src, res, user_data), None
        )

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
                task.return_value(GLib.Bytes.new(tile))

        task.run_in_thread(read_archive)

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
