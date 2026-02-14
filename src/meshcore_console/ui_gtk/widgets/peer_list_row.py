"""Reusable peer list row for contacts/repeaters lists."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Pango

from meshcore_console.core.models import Peer
from meshcore_console.ui_gtk.widgets.node_badge import (
    STYLE_DEFAULT,
    STYLE_REPEATER,
    NodeBadge,
)


class PeerListRow(Gtk.ListBoxRow):
    """A row in the contacts or repeaters list.

    Displays a NodeBadge, peer name, last-seen meta, and signal quality
    indicator with threshold-based CSS classes.

    Usage::

        row = PeerListRow(peer)
        listbox.append(row)
    """

    def __init__(self, peer: Peer) -> None:
        super().__init__()
        self.peer = peer
        self.add_css_class("peer-row")

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        body.set_margin_top(6)
        body.set_margin_bottom(6)
        body.set_margin_start(10)
        body.set_margin_end(10)

        # Node prefix badge (first 2 hex chars of public key)
        prefix_text = (peer.public_key or "")[:2].upper()
        if prefix_text:
            style = STYLE_REPEATER if peer.is_repeater else STYLE_DEFAULT
            badge = NodeBadge(prefix_text, peer.display_name, peer=peer, style=style)
            badge.set_valign(Gtk.Align.CENTER)
            body.append(badge)

        # Left side: name and meta
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)

        name = Gtk.Label(label=peer.display_name)
        name.set_halign(Gtk.Align.START)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(24)
        text_box.append(name)

        # Show last seen time
        if peer.last_advert_time:
            time_str = peer.last_advert_time.strftime("%H:%M")
            meta_text = f"seen {time_str}"
        else:
            meta_text = "not seen"
        meta = Gtk.Label(label=meta_text)
        meta.add_css_class("panel-muted")
        meta.add_css_class("peer-meta")
        meta.set_halign(Gtk.Align.START)
        text_box.append(meta)

        body.append(text_box)

        # Right side: signal indicator
        if peer.signal_quality is not None:
            signal_label = Gtk.Label(label=f"{peer.signal_quality}%")
            signal_label.add_css_class("peer-signal")
            if peer.signal_quality >= 70:
                signal_label.add_css_class("signal-good")
            elif peer.signal_quality >= 40:
                signal_label.add_css_class("signal-fair")
            else:
                signal_label.add_css_class("signal-poor")
            body.append(signal_label)

        self.set_child(body)
