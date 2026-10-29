"""libadwaita version-compatibility helpers.

The app is developed against modern libadwaita (``Adw.ToolbarView`` needs
libadwaita >= 1.4, ``Adw.AlertDialog`` needs >= 1.5) but is also packaged for
Debian 12 (bookworm), which only ships **libadwaita 1.2**. On 1.2 those symbols
don't exist, so building the main window raised ``AttributeError`` inside a
``GLib.idle_add`` callback -- the traceback was swallowed and the app hung on the
"Loading…" screen forever.

These helpers use the modern widgets when they're available and fall back to
equivalents that exist in libadwaita 1.2, so the UI builds on both.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

#: ``Adw.ToolbarView`` exists since libadwaita 1.4.
HAS_TOOLBAR_VIEW = hasattr(Adw, "ToolbarView")
#: ``Adw.AlertDialog`` exists since libadwaita 1.5 (``Adw.MessageDialog`` since 1.2).
HAS_ALERT_DIALOG = hasattr(Adw, "AlertDialog")


def toast_title(text: str) -> str:
    """Escape text for a toast title: libadwaita < 1.4 always parses it as Pango
    markup (``use-markup`` only arrived in 1.4), and toasts carry node names from
    the air, so ``R&D <Hill>`` would blank the toast or restyle it."""
    return GLib.markup_escape_text(text, -1)


def new_toast(text: str) -> Adw.Toast:
    """An Adw.Toast showing ``text`` literally on every libadwaita version."""
    return Adw.Toast.new(toast_title(text))


def choose_save_path(
    parent: Gtk.Window | None,
    title: str,
    initial_name: str,
    patterns: tuple[str, str],
    on_chosen: Callable[[str | None], None],
) -> None:
    """Ask for a file to save to; call ``on_chosen`` with its path, or None.

    Gtk.FileDialog only exists from GTK 4.10; bookworm ships 4.8, where the
    file chooser is Gtk.FileChooserNative.  ``patterns`` is (filter name, glob).
    """
    file_filter = Gtk.FileFilter()
    file_filter.set_name(patterns[0])
    file_filter.add_pattern(patterns[1])

    if hasattr(Gtk, "FileDialog"):
        dialog = Gtk.FileDialog()
        dialog.set_title(title)
        dialog.set_initial_name(initial_name)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)

        def on_done(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                gfile = dlg.save_finish(result)
            except GLib.Error:
                on_chosen(None)  # cancelled
                return
            on_chosen(gfile.get_path() if gfile else None)

        dialog.save(parent, None, on_done)
        return

    chooser = Gtk.FileChooserNative.new(
        title, parent, Gtk.FileChooserAction.SAVE, "_Save", "_Cancel"
    )
    chooser.set_current_name(initial_name)
    chooser.add_filter(file_filter)

    def on_response(native: Gtk.FileChooserNative, response: int) -> None:
        gfile = native.get_file() if response == Gtk.ResponseType.ACCEPT else None
        native.destroy()
        _open_choosers.discard(native)
        on_chosen(gfile.get_path() if gfile else None)

    _open_choosers.add(chooser)  # a native chooser is freed if nothing holds it
    chooser.connect("response", on_response)
    chooser.show()


_open_choosers: set[Gtk.FileChooserNative] = set()


def build_toolbar_page(header_bar: Gtk.Widget, content: Gtk.Widget) -> Gtk.Widget:
    """Return a page with ``header_bar`` above ``content``.

    Uses ``Adw.ToolbarView`` (libadwaita >= 1.4) when available, otherwise a
    vertical ``Gtk.Box`` (libadwaita 1.2 / bookworm).
    """
    if HAS_TOOLBAR_VIEW:
        page = Adw.ToolbarView.new()
        page.add_top_bar(header_bar)
        page.set_content(content)
        return page
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.append(header_bar)
    page.append(content)
    return page


def build_alert_dialog(parent: Gtk.Widget, heading: str, body: str | None = None):
    """Create a confirmation/alert dialog.

    Uses ``Adw.AlertDialog`` (libadwaita >= 1.5) when available, otherwise
    ``Adw.MessageDialog`` (available since 1.2). Both expose the same
    ``add_response`` / ``set_response_appearance`` / ``set_default_response`` /
    ``set_close_response`` / ``set_extra_child`` API, so callers are otherwise
    identical. Present the result with :func:`present_dialog`.
    """
    if HAS_ALERT_DIALOG:
        return Adw.AlertDialog.new(heading, body)
    return Adw.MessageDialog.new(parent, heading, body)


def present_dialog(dialog, parent: Gtk.Widget) -> None:
    """Present a dialog created by :func:`build_alert_dialog`.

    ``Adw.AlertDialog.present`` takes the presenting widget; the older
    ``Adw.MessageDialog.present`` takes no argument (it uses its transient-for).
    """
    if HAS_ALERT_DIALOG:
        dialog.present(parent)
    else:
        dialog.present()


def activate_dialog_response(dialog, response: str) -> None:
    """Activate a dialog response programmatically across libadwaita versions,
    closing the dialog like a click on that response's button would.

    ``Adw.AlertDialog.choose_response`` closes the dialog, but
    ``Adw.MessageDialog.response`` only emits the signal: the dialog stayed open
    and a second Enter ran the response handler again.
    """
    if HAS_ALERT_DIALOG:
        dialog.choose_response(response)
    else:
        dialog.response(response)
        dialog.destroy()  # close() would emit a second, "close" response
