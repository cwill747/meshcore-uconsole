"""Reusable titled content block for detail panels."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


class DetailBlock(Gtk.Box):
    """Vertical box with a muted header label and content.

    For simple text values, pass ``value`` to the constructor. For custom
    content (e.g. a routing path box), call :meth:`set_content` after
    construction.

    Usage::

        block = DetailBlock("Timestamp", "14:32:01.12")
        block = DetailBlock("Routing")
        block.set_content(path_widget)
    """

    WRAP_WIDTH = 24

    def __init__(
        self, title: str, value: str | None = None, *, wrap_chars: int = WRAP_WIDTH
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.add_css_class("analyzer-detail-block")

        header = Gtk.Label(label=title)
        header.add_css_class("panel-muted")
        header.set_halign(Gtk.Align.START)
        self.append(header)

        self._wrap_chars = wrap_chars

        if value is not None:
            wrapped = self._wrap_text(value, wrap_chars) if len(value) > wrap_chars else value
            body = Gtk.Label(label=wrapped)
            body.set_halign(Gtk.Align.START)
            body.set_xalign(0)
            self.append(body)

    def set_content(self, widget: Gtk.Widget) -> None:
        """Append a custom content widget below the header."""
        self.append(widget)

    @staticmethod
    def _wrap_text(text: str, width: int = WRAP_WIDTH) -> str:
        """Insert newlines to wrap text at specified character width."""
        return "\n".join(text[i : i + width] for i in range(0, len(text), width))
