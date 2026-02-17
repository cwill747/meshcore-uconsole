"""Reusable clickable node identifier badge.

Displays a small 2-char hex prefix. Hover shows the node's display name.
Left-click opens a popover with node details. Right-click navigates to the
Peers tab with the node selected.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from meshcore_console.core.models import Peer
from meshcore_console.core.time import to_local

STYLE_DEFAULT = "default"  # Blue (contacts, general nodes)
STYLE_REPEATER = "repeater"  # Amber (repeater/relay hops)
STYLE_SELF = "self"  # Green (self node)


def find_peer_for_hop(peers: list[Peer], hop: str) -> Peer | None:
    """Best-effort match of a path hop identifier to a known peer."""
    hop_lower = hop.lower()
    for peer in peers:
        if peer.display_name == hop:
            return peer
        if peer.peer_id == hop:
            return peer
        if peer.public_key and peer.public_key.lower().startswith(hop_lower):
            return peer
    return None


class NodeBadge(Gtk.Box):
    """Clickable node identifier badge with hover tooltip and click popover.

    Uses a plain Gtk.Box with a styled label to avoid Gtk.MenuButton's
    internal dimming of flat toggle buttons in Adwaita.

    - Left click: opens popover with node details.
    - Right click: navigates to the Peers tab with the node selected.

    Usage::

        badge = NodeBadge("A3", "Alice", peer=alice_peer)
        badge = NodeBadge("B7", "B7", style=STYLE_REPEATER)
        badge = NodeBadge("Me", "You", style=STYLE_SELF)
    """

    def __init__(
        self,
        prefix: str,
        display_name: str,
        peer: Peer | None = None,
        style: str = STYLE_DEFAULT,
    ) -> None:
        super().__init__()
        self.add_css_class("node-badge")

        self._peer = peer
        self._prefix = prefix
        self._display_name = display_name
        self._popover: Gtk.Popover | None = None

        # Inner badge label
        label = Gtk.Label(label=prefix)
        label.add_css_class("node-prefix")
        if style == STYLE_REPEATER:
            label.add_css_class("node-prefix-repeater")
        elif style == STYLE_SELF:
            label.add_css_class("node-prefix-self")
        self.append(label)

        # Tooltip
        self.set_tooltip_text(display_name)

        # Left click → open popover
        left_click = Gtk.GestureClick.new()
        left_click.set_button(1)
        left_click.connect("released", self._on_left_click)
        self.add_controller(left_click)

        # Right click → navigate to Peers tab
        if peer is not None:
            right_click = Gtk.GestureClick.new()
            right_click.set_button(3)
            right_click.connect("released", self._on_right_click)
            self.add_controller(right_click)

    def _on_left_click(
        self, gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float
    ) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self._popover is not None and self._popover.get_visible():
            self._popover.popdown()
            return
        if self._popover is None:
            self._popover = Gtk.Popover.new()
            self._popover.set_parent(self)
            self._popover.set_child(
                self._build_popover(self._prefix, self._display_name, self._peer)
            )
        self._popover.popup()

    def _on_right_click(
        self, gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float
    ) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self._popover is not None and self._popover.get_visible():
            self._popover.popdown()
        self._navigate_to_peer()

    def _navigate_to_peer(self) -> None:
        """Navigate to the Peers tab with this peer selected."""
        if self._peer is None:
            return

        root = self.get_root()
        if root is None:
            return

        stack = getattr(root, "_stack", None)
        if stack is None:
            return

        # Switch to peers view
        stack.set_visible_child_name("peers")

        # Update nav buttons
        nav_buttons = getattr(root, "_nav_buttons", None)
        if nav_buttons:
            for name, btn in nav_buttons.items():
                btn.set_active(name == "peers")

        # Select the peer in the peers view
        peers_widget = stack.get_child_by_name("peers")
        if peers_widget is not None:
            select_fn = getattr(peers_widget, "select_peer", None)
            if select_fn:
                select_fn(self._peer.peer_id)

    @staticmethod
    def _build_popover(prefix: str, display_name: str, peer: Peer | None) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)

        # Display name
        name_label = Gtk.Label(label=display_name)
        name_label.add_css_class("node-popover-name")
        name_label.set_halign(Gtk.Align.START)
        box.append(name_label)

        if peer is None:
            # Minimal info
            id_label = Gtk.Label(label=f"Node: {prefix}")
            id_label.add_css_class("panel-muted")
            id_label.set_halign(Gtk.Align.START)
            box.append(id_label)
            return box

        # Type
        type_text = "Repeater" if peer.is_repeater else "Contact"
        type_label = Gtk.Label(label=type_text)
        type_label.add_css_class("panel-muted")
        type_label.set_halign(Gtk.Align.START)
        box.append(type_label)

        # Signal quality
        if peer.signal_quality is not None:
            sig = Gtk.Label(label=f"Signal: {peer.signal_quality}%")
            sig.set_halign(Gtk.Align.START)
            box.append(sig)

        # RSSI
        if peer.rssi is not None:
            rssi = Gtk.Label(label=f"RSSI: {peer.rssi} dBm")
            rssi.add_css_class("panel-muted")
            rssi.set_halign(Gtk.Align.START)
            box.append(rssi)

        # Last seen
        if peer.last_advert_time:
            time_str = to_local(peer.last_advert_time).strftime("%b %d at %H:%M")
            seen = Gtk.Label(label=f"Seen: {time_str}")
            seen.add_css_class("panel-muted")
            seen.set_halign(Gtk.Align.START)
            box.append(seen)

        # Public key (truncated)
        if peer.public_key:
            chunks = [
                peer.public_key[i : i + 4] for i in range(0, min(len(peer.public_key), 16), 4)
            ]
            key_short = " ".join(chunks) + " ..."
            key = Gtk.Label(label=key_short)
            key.add_css_class("analyzer-raw")
            key.set_halign(Gtk.Align.START)
            box.append(key)

        return box
