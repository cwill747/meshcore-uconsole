from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


class LoadingScreen(Gtk.Box):
    """Lightweight centered loading screen with spinner and status label."""

    def __init__(self, title: str = "MeshCore Console", status: str = "Loading...") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.add_css_class("loading-screen")

        title_label = Gtk.Label(label=title)
        title_label.add_css_class("loading-title")
        self.append(title_label)

        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        self.append(spinner)

        self._status_label = Gtk.Label(label=status)
        self._status_label.add_css_class("loading-status")
        self.append(self._status_label)

    def set_status(self, text: str) -> None:
        self._status_label.set_text(text)
