"""Browse offline coverage on its own map."""

import math
import sqlite3

from gi.repository import Adw, Gtk, Pango, Shumate

from meshcore_console.core.map_view import MAX_ZOOM
from meshcore_console.platform.saved_maps import delete_area
from meshcore_console.ui_gtk.compat import build_alert_dialog, present_dialog


def _bounds(reader, source):
    selection = reader.meta.get("selection")
    if selection and len(selection) == 4 and all(math.isfinite(v) for v in selection):
        return selection
    h = reader.header
    west, east = (source.get_x(0, h[k] / 1e7) / 256 for k in ("min_lon_e7", "max_lon_e7"))
    north, south = (source.get_y(0, h[k] / 1e7) / 256 for k in ("max_lat_e7", "min_lat_e7"))
    return west, north, east if east >= west else east + 1, south


class SavedAreasView(Gtk.Box):
    def __init__(self, owner):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.owner = owner
        self.selected = None
        bar = Gtk.Box(spacing=8)
        back = Gtk.Button(label="Back to map")
        back.connect("clicked", lambda *_: owner._close_saved_areas(self))
        bar.append(back)
        title = Gtk.Label(label="Saved areas")
        title.add_css_class("panel-title")
        bar.append(title)
        self.append(bar)

        body = Gtk.Box(spacing=6, vexpand=True)
        self.append(body)
        overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        body.append(overlay)
        self.map = Shumate.SimpleMap.new()
        self.map.set_map_source(owner._map.get_map_source())
        overlay.set_child(self.map)
        self.viewport = self.map.get_map().get_viewport()
        self.canvas = Gtk.DrawingArea(hexpand=True, vexpand=True, can_target=False)
        overlay.add_overlay(self.canvas)
        self.canvas.set_draw_func(self._draw)
        for prop in ("latitude", "longitude", "zoom-level", "rotation"):
            self.viewport.connect("notify::" + prop, lambda *_: self.canvas.queue_draw())

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.append(side)
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_width=150)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        side.append(scroll)
        self.items = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.items.connect("row-selected", self._select)
        scroll.set_child(self.items)
        self.open_btn = Gtk.Button(label="Open on map")
        self.open_btn.connect("clicked", self._open)
        self.delete_btn = Gtk.Button(label="Delete area")
        self.delete_btn.connect("clicked", self._confirm_delete)
        side.append(self.open_btn)
        side.append(self.delete_btn)
        self.empty = Gtk.Label(label="No saved areas")
        self.items.set_placeholder(self.empty)
        self._reload()
        self.add_tick_callback(self._initial_fit)

    def _initial_fit(self, *_):
        if self.canvas.get_width() < 2:
            return True
        self._fit(list(self.regions.values()))
        return False

    def _reload(self):
        self.selected = None
        self.open_btn.set_sensitive(False)
        self.delete_btn.set_sensitive(False)
        while row := self.items.get_row_at_index(0):
            self.items.remove(row)
        readers = self.owner._archive.readers if self.owner._archive else []
        self.regions = {}
        for reader in readers:
            self.regions[reader] = _bounds(reader, self.map.get_map_source())
            row = Gtk.ListBoxRow()
            row.reader = reader
            label = Gtk.Label(label=reader.meta["name"], xalign=0, max_width_chars=20)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            row.set_child(label)
            self.items.append(row)
        self.canvas.queue_draw()

    def _fit(self, boxes):
        if not boxes:
            self.viewport.set_location(0, 0)
            self.viewport.set_zoom_level(0)
            return
        w, n, e, s = boxes[0]
        for left, top, right, bottom in boxes[1:]:
            shift = round((w + e - left - right) / 2)
            w, e = min(w, left + shift), max(e, right + shift)
            n, s = min(n, top), max(s, bottom)
        scale = min(
            max(1, self.canvas.get_width() - 32) / max(1e-9, 256 * (e - w)),
            max(1, self.canvas.get_height() - 32) / max(1e-9, 256 * (s - n)),
        )
        source = self.map.get_map_source()
        self.viewport.set_rotation(0)
        self.viewport.set_location(
            source.get_latitude(0, (n + s) * 128), source.get_longitude(0, ((w + e) / 2 % 1) * 256)
        )
        self.viewport.set_zoom_level(min(MAX_ZOOM, max(0, math.log2(scale))))

    def _draw(self, _canvas, cr, width, height):
        source = self.map.get_map_source()
        vp = self.viewport
        world = 256 * 2 ** vp.get_zoom_level()
        cx = source.get_x(0, vp.get_longitude()) / 256
        cy = source.get_y(0, vp.get_latitude()) / 256
        radius = math.hypot(width, height) / (2 * world)
        cr.translate(width / 2, height / 2)
        cr.rotate(vp.get_rotation())
        for reader, (w, n, e, s) in self.regions.items():
            for wrap in range(math.ceil(cx - radius - e), math.floor(cx + radius - w) + 1):
                cr.rectangle(
                    (w + wrap - cx) * world, (n - cy) * world, (e - w) * world, (s - n) * world
                )
            color = (1, 0.68, 0.15) if reader is self.selected else (0.13, 0.68, 0.92)
            cr.set_source_rgba(*color, 0.16)
            cr.fill_preserve()
            cr.set_source_rgb(*color)
            cr.set_line_width(3 if reader is self.selected else 2)
            cr.stroke()

    def _select(self, _list, row):
        self.selected = row.reader if row else None
        self.open_btn.set_sensitive(bool(row))
        self.delete_btn.set_sensitive(bool(row))
        if row:
            self._fit([self.regions[self.selected]])
        self.canvas.queue_draw()

    def _open(self, *_):
        reader = self.selected
        lon, lat, zoom = map(float, reader.meta["center"].split(","))
        self.owner._close_saved_areas(self)
        self.owner._visit_area(reader, (lon, lat), zoom)

    def _confirm_delete(self, *_):
        reader = self.selected
        message = (
            "This removes the standalone PMTiles file."
            if reader.path.suffix == ".pmtiles"
            else "Tiles shared with other saved areas will stay available."
        )
        dialog = build_alert_dialog(self.get_root(), f"Delete {reader.path.stem}?", message)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def respond(_dialog, response):
            if response != "delete":
                return
            try:
                delete_area(reader.path)
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.owner._show_toast(f"Could not delete area: {exc}")
                return
            self.owner._area_deleted()
            self.map.set_map_source(self.owner._map.get_map_source())
            self._reload()
            self._fit(list(self.regions.values()))

        dialog.connect("response", respond)
        present_dialog(dialog, self.get_root())
