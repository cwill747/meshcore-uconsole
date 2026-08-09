"""Regression tests for issue #81: hashtag channels must get derived secrets."""

from __future__ import annotations

import pytest

from meshcore_console.meshcore.channel_db import (
    PUBLIC_CHANNEL_SECRET,
    ChannelDatabase,
    derive_channel_secret,
)
from meshcore_console.meshcore.db import open_db


@pytest.fixture()
def conn(tmp_path):
    c = open_db(str(tmp_path / "test.db"))
    yield c
    c.close()


def test_derive_channel_secret_known_value() -> None:
    # Verified against MeshCore: sha256("#chicago")[:32]
    assert derive_channel_secret("chicago") == "c1c289b131e5222370cbc2048445844b"


def test_derive_channel_secret_strips_hash_prefix() -> None:
    assert derive_channel_secret("#chicago") == derive_channel_secret("chicago")


def test_derive_channel_secret_is_case_insensitive() -> None:
    """The secret IS the shared key: #Chicago and #chicago must match, or two
    users who capitalize differently silently cannot decrypt each other."""
    expected = "c1c289b131e5222370cbc2048445844b"  # verified on-device, issue #81
    for variant in ("chicago", "Chicago", "#CHICAGO", " #ChIcAgO "):
        assert derive_channel_secret(variant) == expected


def test_ensure_channel_secret_inserts_derived_secret(conn) -> None:
    db = ChannelDatabase(conn)
    secret = db.ensure_channel_secret("bot")
    assert secret == derive_channel_secret("bot")
    row = db.get_channel("bot")
    assert row is not None
    assert row["secret"] == secret


def test_ensure_channel_secret_does_not_overwrite_existing(conn) -> None:
    db = ChannelDatabase(conn)
    # Public has a hardcoded (non-derived) secret; ensure must not replace it.
    assert db.ensure_channel_secret("Public") == PUBLIC_CHANNEL_SECRET
    assert db.get_channel("Public")["secret"] == PUBLIC_CHANNEL_SECRET
    # Case-insensitive match protects against duplicate rows too.
    assert db.ensure_channel_secret("public") == PUBLIC_CHANNEL_SECRET
    names = [c["name"] for c in db.get_channels()]
    assert names.count("Public") == 1
    assert "public" not in names


def test_remove_channel_is_case_insensitive(conn) -> None:
    db = ChannelDatabase(conn)
    db.add_channel("Bot", derive_channel_secret("Bot"))
    db.remove_channel("bot")
    assert db.get_channel("Bot") is None


def test_client_ensure_channel_stores_secret(tmp_path, monkeypatch) -> None:
    """End-to-end: the '+ Add Channel' path must persist a channel secret."""
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.mock import MockPyMCCoreSession

    db_conn = open_db(str(tmp_path / "client.db"))
    monkeypatch.setattr(client_mod, "open_db", lambda *a, **k: db_conn)

    client = MeshcoreClient(
        session=MockPyMCCoreSession(runtime_config_from_settings(MeshcoreSettings())),
        require_pymc=False,
    )

    # Mirrors the UI '+ Add Channel' handler (messages.py).
    client.ensure_channel("bot", "#bot")

    channel_db = ChannelDatabase(db_conn)
    row = channel_db.get_channel("bot")
    assert row is not None, "ensure_channel must insert into channel_secrets (#81)"
    assert row["secret"] == derive_channel_secret("bot")

    # Removing the channel cleans the secret up again.
    assert client.remove_channel("bot") is True
    assert channel_db.get_channel("bot") is None

    # Public is never removable and keeps its hardcoded secret.
    assert client.remove_channel("public") is False
    assert channel_db.get_channel("Public")["secret"] == PUBLIC_CHANNEL_SECRET


def _client(tmp_path, monkeypatch, name):
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.mock import MockPyMCCoreSession

    db_conn = open_db(str(tmp_path / name))
    monkeypatch.setattr(client_mod, "open_db", lambda *a, **k: db_conn)
    client = MeshcoreClient(
        session=MockPyMCCoreSession(runtime_config_from_settings(MeshcoreSettings())),
        require_pymc=False,
    )
    return client, db_conn


def test_remove_channel_keeps_imported_secret(tmp_path, monkeypatch) -> None:
    """An imported secret cannot be re-derived, so it must survive removal."""
    client, db_conn = _client(tmp_path, monkeypatch, "imported.db")
    channel_db = ChannelDatabase(db_conn)
    channel_db.add_channel("MyPrivate", "00112233445566778899aabbccddeeff")
    client.ensure_channel("MyPrivate", "#MyPrivate")

    assert client.remove_channel("MyPrivate") is True
    row = channel_db.get_channel("MyPrivate")
    assert row is not None, "removing a channel must not destroy an imported PSK"
    assert row["secret"] == "00112233445566778899aabbccddeeff"


@pytest.mark.parametrize(
    ("channel_id", "display_name"),
    [
        ("bot", "#bot"),  # added via '+ Add Channel'
        ("public", "#Public"),  # hardcoded secret, stored capitalised
        ("chicago", "#Chicago"),  # backfilled lowercase, displayed mixed-case
    ],
)
def test_send_uses_the_name_stored_in_channel_secrets(
    tmp_path, monkeypatch, channel_id, display_name
) -> None:
    """pyMC_core matches channels_config by exact name, so the name passed to
    send_group_text must appear verbatim in channel_secrets (issue #81)."""
    client, db_conn = _client(tmp_path, monkeypatch, f"send-{channel_id}.db")
    client.ensure_channel(channel_id, display_name)

    sent: list[str] = []

    async def capture(channel_name: str, message: str):
        sent.append(channel_name)
        return {"ok": True}

    monkeypatch.setattr(client._session, "send_group_text", capture)
    client._connected = True  # skip radio pre-flight; only the send name matters
    client.send_message(peer_id=channel_id, body="hi")

    known = [c["name"] for c in ChannelDatabase(db_conn).get_channels()]
    assert sent == [sent[0]]
    assert sent[0] in known, f"{sent[0]!r} not in channels_config {known}"


# ---------------------------------------------------------------------------
# Migration backfill (issue #81, existing installs)
# ---------------------------------------------------------------------------


def _open_v6_db(path):
    """Open a db and rewind it to v6 (pre-backfill) state."""
    c = open_db(str(path))
    c.execute("UPDATE schema_version SET version = 6")
    c.commit()
    return c


def test_backfill_repairs_channel_added_before_fix(tmp_path) -> None:
    """A user who added #bot before the fix gets a secret on next launch."""
    from meshcore_console.meshcore.db import _backfill_channel_secrets

    conn = _open_v6_db(tmp_path / "old.db")
    # Simulate the buggy state: channels row exists, channel_secrets does not.
    conn.execute(
        "INSERT INTO channels (channel_id, display_name, unread_count, kind) "
        "VALUES ('bot', '#bot', 0, 'group')"
    )
    conn.execute("DELETE FROM channel_secrets WHERE name = 'bot'")
    conn.commit()
    assert ChannelDatabase(conn).get_channel("bot") is None

    _backfill_channel_secrets(conn)
    conn.commit()

    row = ChannelDatabase(conn).get_channel("bot")
    assert row is not None, "backfill must repair pre-existing hashtag channels"
    assert row["secret"] == derive_channel_secret("bot")
    conn.close()


def test_backfill_normalizes_case(tmp_path) -> None:
    """A channel stored with display '#Chicago' must still derive the lowercase
    key other MeshCore devices use (issue #81's verified value)."""
    from meshcore_console.meshcore.db import _backfill_channel_secrets

    conn = _open_v6_db(tmp_path / "case.db")
    conn.execute(
        "INSERT INTO channels (channel_id, display_name, unread_count, kind) "
        "VALUES ('chicago', '#Chicago', 0, 'group')"
    )
    conn.commit()
    _backfill_channel_secrets(conn)
    conn.commit()

    row = ChannelDatabase(conn).get_channel("chicago")
    assert row is not None
    assert row["name"] == "chicago"
    assert row["secret"] == "c1c289b131e5222370cbc2048445844b"
    conn.close()


def test_backfill_skips_dm_channels(tmp_path) -> None:
    from meshcore_console.meshcore.db import _backfill_channel_secrets

    conn = _open_v6_db(tmp_path / "dm.db")
    conn.execute(
        "INSERT INTO channels (channel_id, display_name, unread_count, kind, peer_name) "
        "VALUES ('alice', 'Alice', 0, 'dm', 'Alice')"
    )
    conn.commit()
    _backfill_channel_secrets(conn)
    conn.commit()

    assert ChannelDatabase(conn).get_channel("Alice") is None
    conn.close()


def test_backfill_does_not_overwrite_public(tmp_path) -> None:
    from meshcore_console.meshcore.db import _backfill_channel_secrets

    conn = _open_v6_db(tmp_path / "pub.db")
    conn.execute(
        "INSERT INTO channels (channel_id, display_name, unread_count, kind) "
        "VALUES ('public', '#public', 0, 'group')"
    )
    conn.commit()
    _backfill_channel_secrets(conn)
    conn.commit()

    # Public's hardcoded secret survives; no lowercase duplicate is created.
    assert ChannelDatabase(conn).get_channel("Public")["secret"] == PUBLIC_CHANNEL_SECRET
    names = [c["name"] for c in ChannelDatabase(conn).get_channels()]
    assert names.count("Public") == 1
    assert "public" not in names
    conn.close()


def test_backfill_is_idempotent(tmp_path) -> None:
    from meshcore_console.meshcore.db import _backfill_channel_secrets

    conn = _open_v6_db(tmp_path / "idem.db")
    conn.execute(
        "INSERT INTO channels (channel_id, display_name, unread_count, kind) "
        "VALUES ('bot', '#bot', 0, 'group')"
    )
    conn.commit()
    for _ in range(3):
        _backfill_channel_secrets(conn)
        conn.commit()

    names = [c["name"] for c in ChannelDatabase(conn).get_channels()]
    assert names.count("bot") == 1
    conn.close()


def test_open_db_runs_backfill_on_upgrade(tmp_path) -> None:
    """The repair happens automatically when an old db is opened."""
    path = tmp_path / "upgrade.db"
    conn = _open_v6_db(path)
    conn.execute(
        "INSERT INTO channels (channel_id, display_name, unread_count, kind) "
        "VALUES ('bot', '#bot', 0, 'group')"
    )
    conn.execute("DELETE FROM channel_secrets WHERE name = 'bot'")
    conn.commit()
    conn.close()

    # Reopening triggers _migrate -> _backfill_channel_secrets.
    conn = open_db(str(path))
    row = ChannelDatabase(conn).get_channel("bot")
    assert row is not None, "open_db must repair pre-fix databases (#81)"
    assert row["secret"] == derive_channel_secret("bot")
    conn.close()
