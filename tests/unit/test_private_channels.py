"""Regression tests for issue #101: private channel key functionality.

The '+ Add Channel' dialog and ``radio_cli import-channel`` must accept a
caller-supplied PSK so users can join channels whose key is not derived from
the channel name.
"""

from __future__ import annotations

import pytest

from meshcore_console.meshcore.channel_db import (
    normalize_channel_name,
    normalize_channel_secret,
)
from meshcore_console.meshcore.db import open_db


@pytest.fixture()
def conn(tmp_path):
    c = open_db(str(tmp_path / "test.db"))
    yield c
    c.close()


def _client(tmp_path, monkeypatch, name):
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.mock import MockOpenHopCoreSession

    db_conn = open_db(str(tmp_path / name))
    monkeypatch.setattr(client_mod, "open_db", lambda *a, **k: db_conn)
    client = MeshcoreClient(
        session=MockOpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings())),
        require_openhop=False,
    )
    return client, db_conn


# ---------------------------------------------------------------------------
# normalize_channel_secret
# ---------------------------------------------------------------------------


def test_normalize_channel_secret_accepts_valid_key() -> None:
    key = "00112233445566778899aabbccddeeff"
    assert normalize_channel_secret(key) == key


def test_normalize_channel_secret_uppercase_and_whitespace() -> None:
    key = "  00112233445566778899AABBCCDDEEFF  "
    assert normalize_channel_secret(key) == "00112233445566778899aabbccddeeff"


def test_normalize_channel_secret_strips_0x_prefix() -> None:
    assert normalize_channel_secret("0x00112233445566778899aabbccddeeff") == (
        "00112233445566778899aabbccddeeff"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "00112233445566778899aabbccddee",  # too short
        "00112233445566778899aabbccddeeff00",  # too long
        "zz112233445566778899aabbccddeeff",  # non-hex
        "0x",  # prefix only
    ],
)
def test_normalize_channel_secret_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_channel_secret(bad)


# ---------------------------------------------------------------------------
# MeshcoreClient.import_channel
# ---------------------------------------------------------------------------


def test_import_channel_stores_supplied_secret(tmp_path, monkeypatch) -> None:
    """A private channel's PSK must be stored verbatim, not derived (#101)."""
    from meshcore_console.meshcore.channel_db import (
        ChannelDatabase,
        derive_channel_secret,
    )

    client, db_conn = _client(tmp_path, monkeypatch, "private.db")
    secret = "00112233445566778899aabbccddeeff"
    channel = client.import_channel("MyPrivate", "#MyPrivate", secret)

    assert channel.channel_id == "myprivate"
    assert channel.kind == "group"

    row = ChannelDatabase(db_conn).get_channel("myprivate")
    assert row is not None
    assert row["secret"] == secret
    assert row["secret"] != derive_channel_secret("myprivate")


def test_import_channel_rejects_bad_secret_without_writing(tmp_path, monkeypatch) -> None:
    from meshcore_console.meshcore.channel_db import ChannelDatabase

    client, db_conn = _client(tmp_path, monkeypatch, "badsecret.db")
    with pytest.raises(ValueError):
        client.import_channel("oops", "#oops", "not-a-key")

    names = [c["name"] for c in ChannelDatabase(db_conn).get_channels()]
    assert "oops" not in names
    assert all(c.channel_id != "oops" for c in client.list_channels())


def test_import_channel_name_is_normalized(tmp_path, monkeypatch) -> None:
    client, db_conn = _client(tmp_path, monkeypatch, "norm.db")
    channel = client.import_channel("  #Emergency ", "  #Emergency ", "AB" * 16)
    assert channel.channel_id == "emergency"
    assert channel.display_name == "#emergency"
    assert normalize_channel_name("  #Emergency ") == "emergency"


def test_import_channel_send_uses_stored_name(tmp_path, monkeypatch) -> None:
    """Send path must resolve the imported channel by its stored name."""
    client, db_conn = _client(tmp_path, monkeypatch, "importsend.db")
    client.import_channel("ops", "#ops", "0123456789abcdef0123456789abcdef")

    sent: list[tuple[str, str]] = []

    async def capture(channel_name: str, message: str, region_scope: str | None = None):
        sent.append((channel_name, message))
        return {"ok": True}

    monkeypatch.setattr(client._session, "send_group_text", capture)
    client._connected = True
    client.send_message(peer_id="ops", body="hello")

    assert sent == [("ops", "hello")]


def test_imported_secret_survives_channel_removal(tmp_path, monkeypatch) -> None:
    """Removing the UI channel must not destroy an unrecoverable PSK (#81 rule
    still applies to imported keys)."""
    from meshcore_console.meshcore.channel_db import ChannelDatabase

    client, db_conn = _client(tmp_path, monkeypatch, "survive.db")
    client.import_channel("vault", "#vault", "feedface" + "0123456789abcdef" + "55667788")
    assert client.remove_channel("vault") is True
    row = ChannelDatabase(db_conn).get_channel("vault")
    assert row is not None
    assert row["secret"] == "feedface0123456789abcdef55667788"


# ---------------------------------------------------------------------------
# Mock client parity (protocol conformance for mock mode)
# ---------------------------------------------------------------------------


def test_mock_client_import_channel() -> None:
    from meshcore_console.mock.client import MockMeshcoreClient

    client = MockMeshcoreClient()
    channel = client.import_channel("secret", "#secret", "00112233445566778899aabbccddeeff")
    assert channel.channel_id == "secret"
    assert channel.kind == "group"
    assert client._channel_secrets["secret"] == "00112233445566778899aabbccddeeff"

    with pytest.raises(ValueError):
        client.import_channel("bad", "#bad", "xyz")
