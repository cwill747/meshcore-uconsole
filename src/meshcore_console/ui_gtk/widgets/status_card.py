from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk


class StatusCard(Gtk.Box):
    def __init__(self, title: str, value: str, *, min_width: int = 190) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("metric-card")
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_size_request(min_width, -1)

        title_label = Gtk.Label.new(title)
        title_label.add_css_class("metric-title")
        title_label.set_halign(Gtk.Align.START)
        self.append(title_label)

        value_label = Gtk.Label.new(value)
        value_label.add_css_class("metric-value")
        value_label.set_halign(Gtk.Align.START)
        self.append(value_label)
