"""@Mention parsing for chat messages.

Scans message text for ``@PeerName`` tokens and returns Pango markup
with clickable ``<a href="mention:peer_id">`` links for known peers.
"""

from __future__ import annotations

from gi.repository import GLib

from meshcore_console.core.models import Peer


def parse_mentions(body: str, peers: list[Peer]) -> str:
    """Return Pango markup with known peer names wrapped as links.

    Matching is case-insensitive and greedy (longest name wins).
    Unmatched ``@`` tokens pass through as plain escaped text.
    """
    if not peers or "@" not in body:
        return GLib.markup_escape_text(body)

    # Build lookup sorted longest-first for greedy matching
    name_map: list[tuple[str, Peer]] = sorted(
        ((p.display_name, p) for p in peers if p.display_name),
        key=lambda t: len(t[0]),
        reverse=True,
    )

    result: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "@":
            matched = False
            for name, peer in name_map:
                candidate = body[i + 1 : i + 1 + len(name)]
                if candidate.lower() == name.lower():
                    escaped_name = GLib.markup_escape_text(candidate)
                    peer_id = GLib.markup_escape_text(peer.peer_id)
                    result.append(f'<a href="mention:{peer_id}">@{escaped_name}</a>')
                    i += 1 + len(name)
                    matched = True
                    break
            if not matched:
                result.append(GLib.markup_escape_text("@"))
                i += 1
        else:
            # Collect plain text until next '@'
            start = i
            while i < len(body) and body[i] != "@":
                i += 1
            result.append(GLib.markup_escape_text(body[start:i]))

    return "".join(result)
