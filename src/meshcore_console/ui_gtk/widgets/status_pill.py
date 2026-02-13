from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


class StatusPill(Gtk.Label):
    def __init__(self, label: str, state: str = "ok") -> None:
        super().__init__(label=label)
        self.add_css_class("status-pill")
        self.set_state(state)

    def set_state(self, state: str) -> None:
        for name in ("ok", "warn", "offline"):
            self.remove_css_class(name)
        self.add_css_class(state)
