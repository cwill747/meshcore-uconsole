"""@Mention parsing for chat messages.

Scans message text for ``@[PeerName]`` tokens and returns Pango markup
with clickable ``<a href="mention:peer_id">`` links for known peers.
Brackets are stripped in the rendered output.
"""

from __future__ import annotations

from gi.repository import GLib

from meshcore_console.core.models import Peer


def _escape(text: str) -> str:
    """Markup-escape *text*, passing ``-1`` so GLib computes byte length itself.

    ``GLib.markup_escape_text(s)`` can auto-fill the length parameter with
    ``len(s)`` (code-point count), which truncates multi-byte characters
    like emoji.  Passing ``-1`` avoids this.
    """
    return GLib.markup_escape_text(text, -1)


def parse_mentions(body: str, peers: list[Peer]) -> str:
    """Return Pango markup with ``@[PeerName]`` mentions wrapped as links.

    Brackets are consumed and not displayed. Unmatched ``@[...]`` tokens
    pass through as plain escaped text (with brackets intact).
    """
    if not peers or "@" not in body:
        return _escape(body)

    name_lookup: dict[str, Peer] = {p.display_name.lower(): p for p in peers if p.display_name}

    result: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "@" and i + 1 < len(body) and body[i + 1] == "[":
            close = body.find("]", i + 2)
            if close != -1:
                inner = body[i + 2 : close]
                peer = name_lookup.get(inner.lower())
                if peer is not None:
                    escaped_name = _escape(inner)
                    peer_id = _escape(peer.peer_id)
                    result.append(f'<a href="mention:{peer_id}">@{escaped_name}</a>')
                    i = close + 1
                    continue
            # No match — emit the '@' and let '[' be picked up as plain text
            result.append("@")
            i += 1
        elif body[i] == "@":
            result.append("@")
            i += 1
        else:
            start = i
            while i < len(body) and body[i] != "@":
                i += 1
            result.append(_escape(body[start:i]))

    return "".join(result)
