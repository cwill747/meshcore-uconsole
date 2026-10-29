"""Reusable mesh routing path visualization."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from meshcore_console.core.models import Peer
from meshcore_console.ui_gtk.widgets.node_badge import (
    STYLE_REPEATER,
    NodeBadge,
    find_peer_for_hop,
)


class PathVisualization(Gtk.FlowBox):
    """Wrapping chain of NodeBadge → arrow → NodeBadge showing a mesh path.

    Uses ``Gtk.FlowBox`` so long paths (many hops) wrap to multiple rows
    instead of overflowing the panel horizontally.

    Usage::

        path = PathVisualization(
            hops=peer.last_path,
            peers=all_peers,
            start=("Me", "You (this node)", None, STYLE_SELF),
            end=(prefix, peer.display_name, peer, STYLE_DEFAULT),
        )
    """

    def __init__(
        self,
        hops: list[str],
        peers: list[Peer],
        *,
        arrow: str = "→",
        start: tuple[str, str, Peer | None, str] | None = None,
        end: tuple[str, str, Peer | None, str] | None = None,
    ) -> None:
        super().__init__()
        self.set_selection_mode(Gtk.SelectionMode.NONE)
        self.set_homogeneous(False)
        self.set_row_spacing(4)
        self.set_column_spacing(2)
        # FILL, not START: aligned START, the FlowBox is given the width it
        # asks for at its one-row height, which is one badge wide; it then
        # stacked the badges one per row inside a one-row allocation, drawing
        # them over the widgets below (e.g. the public key in Peers).
        self.set_halign(Gtk.Align.FILL)
        self.set_min_children_per_line(1)
        self.set_max_children_per_line(6)
        self.add_css_class("path-visualization")

        if start is not None:
            prefix, name, peer, style = start
            self.append(self._hop_pair(arrow, prefix, name, peer, style, hide_arrow=True))

        # ``hops`` come as the packet carried them: each relay appends its
        # hash, so the sender's side is first and our neighbour last.  The
        # chain starts at this device, so it walks them backwards; drawn in
        # list order, the relay next to the sender stood next to "Me".
        for hop in reversed(hops):
            hop_peer = find_peer_for_hop(peers, hop)
            hop_name = hop_peer.display_name if hop_peer else hop
            hop_prefix = hop.upper() if len(hop) <= 6 else hop[:2].upper()
            self.append(self._hop_pair(arrow, hop_prefix, hop_name, hop_peer, STYLE_REPEATER))

        if end is not None:
            prefix, name, peer, style = end
            self.append(self._hop_pair(arrow, prefix, name, peer, style))

    @staticmethod
    def _hop_pair(
        arrow: str,
        prefix: str,
        name: str,
        peer: Peer | None,
        style: str,
        *,
        hide_arrow: bool = False,
    ) -> Gtk.Box:
        pair = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        pair.set_halign(Gtk.Align.START)  # a line's spare width goes between pairs
        lbl = Gtk.Label(label=arrow)
        lbl.add_css_class("panel-muted")
        if hide_arrow:
            lbl.set_opacity(0)
        pair.append(lbl)
        pair.append(NodeBadge(prefix, name, peer=peer, style=style))
        return pair
