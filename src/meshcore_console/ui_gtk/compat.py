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

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

#: ``Adw.ToolbarView`` exists since libadwaita 1.4.
HAS_TOOLBAR_VIEW = hasattr(Adw, "ToolbarView")
#: ``Adw.AlertDialog`` exists since libadwaita 1.5 (``Adw.MessageDialog`` since 1.2).
HAS_ALERT_DIALOG = hasattr(Adw, "AlertDialog")


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
    ``set_close_response`` / ``set_extra_child`` / ``response`` API, so callers
    are otherwise identical. Present the result with :func:`present_dialog`.
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
