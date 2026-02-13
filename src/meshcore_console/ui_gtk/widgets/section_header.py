from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


class SectionHeader(Gtk.Box):
    def __init__(self, title: str, subtitle: str | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        title_label = Gtk.Label.new(title)
        title_label.add_css_class("section-title")
        title_label.add_css_class("title-2")
        title_label.set_halign(Gtk.Align.START)
        self.append(title_label)

        if subtitle:
            # Truncate subtitle to prevent natural width explosion in GTK4
            subtitle_label = Gtk.Label.new(subtitle[:50])
            subtitle_label.add_css_class("section-subtitle")
            subtitle_label.set_halign(Gtk.Align.START)
            self.append(subtitle_label)
