"""Reusable detail row widget for label/value pairs."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


class DetailRow(Gtk.Box):
    """A horizontal box with a muted label and value."""

    def __init__(self, label: str, value: str, spacing: int = 8) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)

        self._label = Gtk.Label(label=label)
        self._label.add_css_class("panel-muted")
        self._label.set_halign(Gtk.Align.START)
        self.append(self._label)

        self._value = Gtk.Label(label=value)
        self._value.set_halign(Gtk.Align.START)
        self.append(self._value)

    def set_label(self, label: str) -> None:
        """Update the label text."""
        self._label.set_text(label)

    def set_value(self, value: str) -> None:
        """Update the value text."""
        self._value.set_text(value)
