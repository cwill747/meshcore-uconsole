"""Reusable date divider row for the analyzer stream."""

from __future__ import annotations

from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


class DaySeparator(Gtk.ListBoxRow):
    """Non-selectable row with left rule — date label — right rule.

    Usage::

        sep = DaySeparator("2026-02-12")
        stream.append(sep)
    """

    def __init__(self, date_str: str) -> None:
        super().__init__()
        self.set_selectable(False)
        self.set_activatable(False)
        self.add_css_class("analyzer-day-separator")

        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            display = dt.strftime("%b %d, %Y")
        except ValueError:
            display = date_str

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_halign(Gtk.Align.FILL)

        left_rule = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
        left_rule.set_hexpand(True)
        left_rule.set_valign(Gtk.Align.CENTER)
        box.append(left_rule)

        label = Gtk.Label(label=display)
        label.add_css_class("day-separator-label")
        box.append(label)

        right_rule = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
        right_rule.set_hexpand(True)
        right_rule.set_valign(Gtk.Align.CENTER)
        box.append(right_rule)

        self.set_child(box)
