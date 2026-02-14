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


class PathVisualization(Gtk.Box):
    """Horizontal chain of NodeBadge → arrow → NodeBadge showing a mesh path.

    The ``start`` and ``end`` parameters define the terminal nodes. Each hop in
    between is looked up against the known peers list for display names.

    Usage::

        # Peer details: Me → hop1 → hop2 → Peer
        path = PathVisualization(
            hops=peer.last_path,
            peers=all_peers,
            start=("Me", "You (this node)", None, STYLE_SELF),
            end=(prefix, peer.display_name, peer, STYLE_DEFAULT),
        )

        # Analyzer routing: Me ← hop1 ← hop2 ← Sender
        path = PathVisualization(
            hops=packet.path_hops,
            peers=all_peers,
            arrow="←",
            start=("Me", "You (this node)", None, STYLE_SELF),
            end=(prefix, sender_name, sender_peer, STYLE_DEFAULT),
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
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.set_halign(Gtk.Align.START)

        if start is not None:
            prefix, name, peer, style = start
            self.append(NodeBadge(prefix, name, peer=peer, style=style))

        for hop in hops:
            self._append_arrow(arrow)
            hop_peer = find_peer_for_hop(peers, hop)
            hop_name = hop_peer.display_name if hop_peer else hop
            hop_prefix = hop[:2].upper()
            self.append(NodeBadge(hop_prefix, hop_name, peer=hop_peer, style=STYLE_REPEATER))

        if end is not None:
            self._append_arrow(arrow)
            prefix, name, peer, style = end
            self.append(NodeBadge(prefix, name, peer=peer, style=style))

    def _append_arrow(self, arrow: str) -> None:
        label = Gtk.Label(label=arrow)
        label.add_css_class("panel-muted")
        self.append(label)
