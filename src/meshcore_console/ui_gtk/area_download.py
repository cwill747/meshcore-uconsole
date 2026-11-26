"""Map rectangle selection and explicit offline-area download."""

import math
import threading

from gi.repository import Gdk, Gio, GLib, Gtk

from meshcore_console.core.map_view import MAX_ZOOM
from meshcore_console.platform.offline_area import tiles_directory
from meshcore_console.platform.offline_area import (
    archive_path,
    tile_ranges,
)

from meshcore_console.platform.protomaps import (
    BASEMAP_MAX_ZOOM,
    latest_build,
    remote_reader,
)
from meshcore_console.platform.saved_maps import save_area


def select_area(view, overlay):
    canvas = Gtk.DrawingArea(hexpand=True, vexpand=True)
    overlay.add_overlay(canvas)
    bar = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
    bar.set_margin_top(12)
    bar.add_css_class("map-area-picker")
    bar.append(Gtk.Label(label="Drag a rectangle or choose an area"))
    rect = []

    def remove(*_):
        view._area_picker_focus = None
        overlay.remove_overlay(bar)
        overlay.remove_overlay(canvas)
        view._area_btn.set_sensitive(view._online_tiles)
        if view._area_btn.is_sensitive():
            view._area_btn.grab_focus()

    def draw(_area, cr, _w, _h):
        if rect:
            cr.rectangle(*rect)
            cr.set_source_rgba(0.1, 0.6, 0.8, 0.30)
            cr.fill_preserve()
            cr.set_source_rgb(0.2, 0.85, 1)
            cr.set_line_width(2)
            cr.stroke()

    canvas.set_draw_func(draw)
    drag = Gtk.GestureDrag.new()
    canvas.add_controller(drag)

    def begin(_gesture, x, y):
        rect[:] = [x, y, 0, 0]

    def update(_gesture, dx, dy):
        rect[2:] = [dx, dy]
        canvas.queue_draw()

    def confirm_selection(x1, y1, x2, y2):
        vp = view._viewport
        source = view._map.get_map_source()
        level = vp.get_zoom_level()
        world = 256 * 2**level
        cx = source.get_x(0, vp.get_longitude()) / 256
        cy = source.get_y(0, vp.get_latitude()) / 256
        c, s = math.cos(vp.get_rotation()), math.sin(vp.get_rotation())
        points = []
        for x in (x1, x2):
            for y in (y1, y2):
                u = (x - canvas.get_width() / 2) / world
                v = (y - canvas.get_height() / 2) / world
                points.append((cx + c * u + s * v, cy - s * u + c * v))
        xs, ys = zip(*points)
        bounds = min(xs), max(0, min(ys)), max(xs), min(1, max(ys))
        center = (
            source.get_longitude(0, ((bounds[0] + bounds[2]) / 2 % 1) * 256),
            source.get_latitude(0, (bounds[1] + bounds[3]) * 128),
        )
        remove()
        download_dialog(view, bounds, center, round(level))

    def end(_gesture, dx, dy):
        if abs(dx) >= 4 and abs(dy) >= 4:
            confirm_selection(rect[0], rect[1], rect[0] + dx, rect[1] + dy)

    def choose_extent(_button, inset):
        width, height = canvas.get_width(), canvas.get_height()
        if width and height:
            confirm_selection(
                width * inset, height * inset, width * (1 - inset), height * (1 - inset)
            )

    center = Gtk.Button(label="Center third")
    center.set_tooltip_text("Select the middle cell of a 3 × 3 grid over the map")
    center.connect("clicked", choose_extent, 1 / 3)
    bar.append(center)
    whole = Gtk.Button(label="Whole view")
    whole.set_tooltip_text("Select the entire visible map")
    whole.connect("clicked", choose_extent, 0)
    bar.append(whole)
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", remove)
    bar.append(cancel)

    def cancel_with_escape(_controller, keyval, _keycode, _state):
        if keyval != Gdk.KEY_Escape:
            return False
        remove()
        return True

    keys = Gtk.EventControllerKey.new()
    keys.connect("key-pressed", cancel_with_escape)
    bar.add_controller(keys)
    view._area_picker_focus = center
    center.connect("map", lambda button: button.grab_focus())
    overlay.add_overlay(bar)

    drag.connect("drag-begin", begin)
    drag.connect("drag-update", update)
    drag.connect("drag-end", end)


def download_dialog(view, bounds, center, level, refresh_path=None):
    window = Gtk.Window(
        title="Refresh saved area" if refresh_path else "Download map area",
        transient_for=view.get_root(),
        modal=True,
    )
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for side in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{side}")(12)
    window.set_child(box)
    name = Gtk.Entry()
    zoom = Gtk.SpinButton.new_with_range(0, MAX_ZOOM, 1)
    zoom.set_value(level)
    if refresh_path:
        name.set_text(refresh_path.stem)
        name.set_editable(False)
        zoom.set_sensitive(False)
    for title, field in (("Area name", name), ("Maximum zoom", zoom)):
        label = Gtk.Label(label=title, xalign=0)
        label.set_mnemonic_widget(field)
        box.append(label)
        box.append(field)
    status = Gtk.Label(xalign=0, wrap=True)
    box.append(status)
    button = Gtk.Button(label="Refresh" if refresh_path else "Download")
    actions = Gtk.Box(spacing=8)
    cancel_button = Gtk.Button(label="Cancel")
    actions.append(cancel_button)
    actions.append(button)
    box.append(actions)
    cancel = Gio.Cancellable()

    def count(*_):
        total = sum(
            len(xs) * len(ys)
            for z in range(min(zoom.get_value_as_int(), BASEMAP_MAX_ZOOM) + 1)
            for xs, ys in [tile_ranges(bounds, z)]
        )
        message = f"Protomaps · up to {total:,} tiles"
        if zoom.get_value_as_int() > BASEMAP_MAX_ZOOM:
            message += f" · Above zoom {BASEMAP_MAX_ZOOM}, the map magnifies downloaded tiles."
        status.set_text(message)
        return total

    zoom.connect("value-changed", count)
    count()
    running = False

    def close_requested(*_):
        if running:
            cancel.cancel()
            cancel_button.set_sensitive(False)
            status.set_text("Cancelling…")
        return running

    window.connect("close-request", close_requested)
    cancel_button.connect("clicked", lambda *_: window.close())

    def start(*_):
        nonlocal running
        if not view._online_tiles:
            status.set_text("Enable Downloads in Settings.")
            return
        try:
            path = archive_path(tiles_directory(), name.get_text())
            if path.exists() and refresh_path is None:
                raise ValueError("An area with that name already exists.")
        except ValueError as exc:
            status.set_text(str(exc))
            return
        z = zoom.get_value_as_int()
        total = count()
        for field in (name, zoom, button):
            field.set_sensitive(False)
        running = True
        completed = 0

        def tick():
            if cancel.is_cancelled():
                return True
            status.set_text(f"Saving {completed:,} of {total:,} tiles")
            return True

        timer = GLib.timeout_add(250, tick)

        def finish(error):
            nonlocal running
            running = False
            GLib.source_remove(timer)
            if error and cancel.is_cancelled():
                window.close()
                view._show_toast("Download cancelled")
                return False
            if error:
                status.set_text(error)
                for field in (name, zoom, button):
                    field.set_sensitive(field is not zoom or refresh_path is None)
            else:
                window.close()
                view._area_saved(path.with_suffix(".area"), center, z)
            return False

        def run():
            def progress(n):
                nonlocal completed
                completed = n

            try:
                save_area(
                    path,
                    bounds,
                    z,
                    center,
                    lambda: remote_reader(latest_build(), cancel.is_cancelled),
                    cancel.is_cancelled,
                    progress,
                    replace=refresh_path is not None,
                )
            except Exception as exc:
                GLib.idle_add(finish, str(exc))
            else:
                GLib.idle_add(finish, None)

        threading.Thread(target=run, daemon=True).start()

    button.connect("clicked", start)
    window.present()
