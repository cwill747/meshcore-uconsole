"""Map markers follow moves and renames, not just the number of peers."""

from __future__ import annotations

from meshcore_console.core.models import Peer
from meshcore_console.ui_gtk.views.map import marker_state


def peer(name: str, lat: float | None, lon: float | None) -> Peer:
    return Peer(peer_id=name, display_name=name, latitude=lat, longitude=lon)


def test_state_changes_on_move_and_rename_with_the_same_count() -> None:
    base = marker_state([peer("Bergblick", 51.3, 12.4), peer("Nowhere", None, None)])
    assert marker_state([peer("Bergblick", 51.4, 12.4)]) != base  # moved
    assert marker_state([peer("Talblick", 51.3, 12.4)]) != base  # renamed
    assert marker_state([peer("Bergblick", 51.3, 12.4)]) == base  # unlocated peers ignored
