from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Sequence

from meshcore_console.meshcore.logging_setup import configure_logging, set_stderr_level

# Default to no accessibility backend — the target uConsole/Pi hardware
# does not run AT-SPI, and GTK4 spams warnings when it's missing.
# Users can still override via GTK_A11Y=atspi if they need screen-reader support.
os.environ.setdefault("GTK_A11Y", "none")

import gi

configure_logging()

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

        # Apply persisted log level unless LOG_LEVEL env var is set
        if not os.environ.get("LOG_LEVEL"):
            set_stderr_level(self.service.get_settings().log_level)

    def do_shutdown(self) -> None:
        self.service.disconnect()
        Adw.Application.do_shutdown(self)

    def do_activate(self) -> None:
        from meshcore_console.ui_gtk.debug_hooks import install as install_debug_hooks

        _load_css()
        install_debug_hooks()
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
