"""Reusable empty-state placeholder for lists and panels."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


class EmptyState(Gtk.Box):
    """Centered, muted label shown when a list or panel has no data.

    Usage::

        placeholder = EmptyState("No contacts yet")
        placeholder = EmptyState("Select a peer to view details", vexpand=True)
    """

    def __init__(self, message: str, *, vexpand: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_vexpand(vexpand)

        self._label = Gtk.Label(label=message)
        self._label.add_css_class("panel-muted")
        self._label.set_halign(Gtk.Align.CENTER)
        self._label.set_valign(Gtk.Align.CENTER)
        self._label.set_margin_top(24)
        self._label.set_margin_bottom(24)
        self.append(self._label)
