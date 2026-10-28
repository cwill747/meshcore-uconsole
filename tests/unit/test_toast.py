"""Toast titles show text literally (libadwaita < 1.4 parses them as markup)."""

from __future__ import annotations

from meshcore_console.ui_gtk.compat import toast_title


def test_names_from_the_air_are_escaped() -> None:
    assert toast_title("Discovered 1 node: R&D <Hill>") == (
        "Discovered 1 node: R&amp;D &lt;Hill&gt;"
    )
    assert toast_title('<span foreground="red">x</span>') == (
        "&lt;span foreground=&quot;red&quot;&gt;x&lt;/span&gt;"
    )
    assert toast_title("Sent flood advert") == "Sent flood advert"
