"""The Admin view rebuilds its repeater list whenever what it shows changes."""

from __future__ import annotations

from meshcore_console.core.models import Peer
from meshcore_console.ui_gtk.views.admin import repeater_list_state


def peer(name: str, key: str, repeater: bool = True) -> Peer:
    return Peer(peer_id=key[:16], display_name=name, public_key=key, is_repeater=repeater)


def test_state_tracks_repeaters_names_and_logins() -> None:
    base = [peer("Bergblick", "aa" * 32), peer("Bob", "bb" * 32, repeater=False)]
    state = repeater_list_state(base, [])

    assert repeater_list_state(list(reversed(base)), []) == state  # order-independent
    assert repeater_list_state([*base, peer("Bob", "bb" * 32, repeater=False)], []) == state
    assert repeater_list_state([*base, peer("Talblick", "cc" * 32)], []) != state  # new repeater
    assert repeater_list_state([peer("Bergblick Sued", "aa" * 32)], []) != state  # renamed
    assert repeater_list_state(base, ["Bergblick"]) != state  # logged in
