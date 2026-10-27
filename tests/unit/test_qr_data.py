"""The contact QR link survives names with URL syntax in them."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from meshcore_console.ui_gtk.widgets.qr_dialog import contact_qr_data


def test_name_is_url_encoded() -> None:
    link = contact_qr_data("R&D Hill #2", "ab" * 32)
    query = parse_qs(urlparse(link).query)
    assert query == {"name": ["R&D Hill #2"], "key": ["ab" * 32]}
    assert link.startswith("meshcore://contact?name=R%26D%20Hill%20%232&key=")


def test_without_key() -> None:
    assert contact_qr_data("DF9ES", None) == "meshcore://contact?name=DF9ES"
