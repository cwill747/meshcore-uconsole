from __future__ import annotations

import logging
import os
import signal
from pathlib import Path
from typing import Sequence

import gi


def _configure_logging() -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if os.environ.get("MESHCORE_DEBUG", "0") == "1" else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(name)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )


_configure_logging()

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gio, Gtk

from meshcore_console.ui_gtk.windows.main_window import MainWindow

APP_ID = "com.meshcore.Console"


def _load_css() -> None:
    resources = Path(__file__).parent / "ui_gtk" / "resources"
    css_files = ("tokens.css", "app.css", "theme.css")
    display = Gdk.Display.get_default()
    if display is None:
        return

    for name in css_files:
        css_path = resources / name
        if not css_path.exists():
            continue
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css_path))
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


class MeshcoreApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

        use_mock = os.environ.get("MESHCORE_MOCK", "0") == "1"
        if use_mock:
            from meshcore_console.mock import MockMeshcoreClient

            self.service = MockMeshcoreClient()
        else:
            from meshcore_console.meshcore.client import MeshcoreClient

            self.service = MeshcoreClient()

    def do_activate(self) -> None:
        _load_css()
        window = self.props.active_window
        if window is None:
            window = MainWindow(application=self, service=self.service)
        window.present()


def run(argv: Sequence[str] | None = None) -> int:
    app = MeshcoreApplication()
    # Ensure Ctrl-C works even when the radio is in a bad state.
    # GTK's main loop doesn't forward SIGINT by default on all platforms.
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, lambda: app.quit() or True)
    return app.run(argv)
