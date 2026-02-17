"""Tests for SQLite schema, migrations, and all store round-trips.

These tests are specifically designed to catch schema breakages:
- Column count/order mismatches between DDL and store SQL
- Missing or renamed columns
- Type round-trip failures (bool↔int, datetime↔ISO str, list↔JSON)
- Migration failures or non-idempotency
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from meshcore_console.core.models import Channel, Message, Peer
from meshcore_console.meshcore.channel_db import ChannelDatabase, PUBLIC_CHANNEL_SECRET
from meshcore_console.meshcore.db import MIGRATIONS, _get_version, _migrate, open_db
from meshcore_console.meshcore.packet_store import PacketStore
from meshcore_console.meshcore.settings import MeshcoreSettings
from meshcore_console.meshcore.settings_store import SettingsStore
from meshcore_console.meshcore.state_store import MessageStore, PeerStore, UIChannelStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    """Fresh migrated database connection."""
    c = open_db(str(tmp_path / "test.db"))
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Schema & migration tests
# ---------------------------------------------------------------------------

EXPECTED_TABLES = {
    "schema_version",
    "settings",
    "channels",
    "channel_secrets",
    "peers",
    "messages",
    "packets",
}

EXPECTED_COLUMNS = {
    "settings": ["key", "value"],
    "channels": ["channel_id", "display_name", "unread_count", "peer_name"],
    "channel_secrets": ["name", "secret"],
    "peers": [
        "peer_id",
        "display_name",
        "signal_quality",
        "public_key",
        "last_advert_time",
        "last_path",
        "is_repeater",
        "rssi",
        "snr",
        "latitude",
        "longitude",
        "location_updated",
        "is_favorite",
    ],
    "messages": [
        "message_id",
        "sender_id",
        "body",
        "channel_id",
        "created_at",
        "is_outgoing",
        "path_len",
        "snr",
        "rssi",
        "path_hops",
    ],
    "packets": ["id", "received_at", "data"],
}


def test_open_db_creates_all_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {row[0] for row in rows}
    assert tables == EXPECTED_TABLES


@pytest.mark.parametrize("table,expected_cols", EXPECTED_COLUMNS.items())
def test_table_columns_match_expected(conn, table, expected_cols):
    """Catches column renames, additions, or removals that would break positional SELECTs."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    actual_cols = [row[1] for row in rows]
    assert actual_cols == expected_cols, (
        f"Column mismatch in {table}: expected {expected_cols}, got {actual_cols}"
    )


def test_schema_version_is_set(conn):
    assert _get_version(conn) == len(MIGRATIONS)


def test_open_db_idempotent(tmp_path):
    """Opening the same DB twice must not fail or re-run migrations."""
    path = str(tmp_path / "test.db")
    conn1 = open_db(path)
    conn1.execute("INSERT INTO settings (key, value) VALUES ('x', 'y')")
    conn1.commit()
    conn1.close()

    conn2 = open_db(path)
    row = conn2.execute("SELECT value FROM settings WHERE key='x'").fetchone()
    assert row[0] == "y"
    assert _get_version(conn2) == len(MIGRATIONS)
    conn2.close()


def test_migrate_on_empty_db(tmp_path):
    """Migration from v0 (no tables) to current version succeeds."""
    path = str(tmp_path / "bare.db")
    raw = sqlite3.connect(path)
    assert _get_version(raw) == 0
    _migrate(raw)
    assert _get_version(raw) == len(MIGRATIONS)
    raw.close()


def test_migrate_skips_if_current(conn):
    """Calling _migrate on an already-migrated DB is a no-op."""
    before = _get_version(conn)
    _migrate(conn)
    assert _get_version(conn) == before


# ---------------------------------------------------------------------------
# SettingsStore tests
# ---------------------------------------------------------------------------


def test_settings_load_empty_returns_defaults(conn):
    store = SettingsStore(conn)
    settings = store.load()
    defaults = MeshcoreSettings()
    assert settings.node_name == defaults.node_name
    assert settings.frequency == defaults.frequency
    assert settings.share_position == defaults.share_position


def test_settings_round_trip_all_types(conn):
    """Every field of MeshcoreSettings survives a save/load cycle.

    Catches: missing columns, type-casting bugs, bool serialization issues.
    """
    store = SettingsStore(conn)
    original = MeshcoreSettings(
        node_name="test-node",
        latitude=45.123,
        longitude=-122.678,
        share_position=True,
        allow_telemetry=False,
        autoconnect=True,
        radio_preset="meshcore-eu",
        frequency=869_525_000,
        bandwidth=250_000,
        spreading_factor=11,
        coding_rate=5,
        tx_power=14,
        preamble_length=8,
        bus_id=0,
        cs_id=1,
        cs_pin=7,
        reset_pin=22,
        busy_pin=23,
        irq_pin=25,
        txen_pin=5,
        rxen_pin=6,
        is_waveshare=True,
        use_dio2_rf=False,
        use_dio3_tcxo=False,
    )
    store.save(original)
    loaded = store.load()

    # Check every field explicitly so a new field added to the dataclass
    # without a matching settings column shows up as a failure.
    assert loaded.node_name == original.node_name
    assert loaded.latitude == original.latitude
    assert loaded.longitude == original.longitude
    assert loaded.share_position is True
    assert loaded.allow_telemetry is False
    assert loaded.autoconnect is True
    assert loaded.radio_preset == original.radio_preset
    assert loaded.frequency == original.frequency
    assert loaded.bandwidth == original.bandwidth
    assert loaded.spreading_factor == original.spreading_factor
    assert loaded.coding_rate == original.coding_rate
    assert loaded.tx_power == original.tx_power
    assert loaded.preamble_length == original.preamble_length
    assert loaded.bus_id == original.bus_id
    assert loaded.cs_id == original.cs_id
    assert loaded.cs_pin == original.cs_pin
    assert loaded.reset_pin == original.reset_pin
    assert loaded.busy_pin == original.busy_pin
    assert loaded.irq_pin == original.irq_pin
    assert loaded.txen_pin == original.txen_pin
    assert loaded.rxen_pin == original.rxen_pin
    assert loaded.is_waveshare is True
    assert loaded.use_dio2_rf is False
    assert loaded.use_dio3_tcxo is False


def test_settings_save_overwrites_previous(conn):
    store = SettingsStore(conn)
    store.save(MeshcoreSettings(node_name="first"))
    store.save(MeshcoreSettings(node_name="second"))
    assert store.load().node_name == "second"


def test_settings_bool_false_round_trip(conn):
    """False booleans must not deserialize as True (common str→bool bug)."""
    store = SettingsStore(conn)
    store.save(MeshcoreSettings(share_position=False, autoconnect=False, is_waveshare=False))
    loaded = store.load()
    assert loaded.share_position is False
    assert loaded.autoconnect is False
    assert loaded.is_waveshare is False


# ---------------------------------------------------------------------------
# MessageStore tests
# ---------------------------------------------------------------------------


def _make_message(
    msg_id="m1",
    sender="alice",
    body="hello",
    channel="public",
    is_outgoing=False,
    path_len=0,
    snr=None,
    rssi=None,
    created_at=None,
):
    return Message(
        message_id=msg_id,
        sender_id=sender,
        body=body,
        channel_id=channel,
        created_at=created_at or datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
        is_outgoing=is_outgoing,
        path_len=path_len,
        snr=snr,
        rssi=rssi,
    )


def test_message_round_trip_all_fields(conn):
    """Every Message field survives append→get_all. Catches column order mismatches."""
    store = MessageStore(conn)
    original = _make_message(
        msg_id="msg-001",
        sender="bob",
        body="test body",
        channel="test-chan",
        is_outgoing=True,
        path_len=3,
        snr=-7.5,
        rssi=-95,
    )
    store.append(original)
    result = store.get_all()

    assert len(result) == 1
    m = result[0]
    assert m.message_id == "msg-001"
    assert m.sender_id == "bob"
    assert m.body == "test body"
    assert m.channel_id == "test-chan"
    assert m.created_at == original.created_at
    assert m.is_outgoing is True
    assert m.path_len == 3
    assert m.snr == -7.5
    assert m.rssi == -95


def test_message_nullable_fields(conn):
    """snr and rssi can be None."""
    store = MessageStore(conn)
    store.append(_make_message(snr=None, rssi=None))
    m = store.get_all()[0]
    assert m.snr is None
    assert m.rssi is None


def test_message_duplicate_ignored(conn):
    store = MessageStore(conn)
    msg = _make_message(msg_id="dup-1")
    store.append(msg)
    store.append(msg)
    assert len(store) == 1


def test_message_get_for_channel(conn):
    store = MessageStore(conn)
    store.append(_make_message(msg_id="a1", channel="alpha"))
    store.append(_make_message(msg_id="b1", channel="beta"))
    store.append(_make_message(msg_id="a2", channel="alpha"))

    alpha = store.get_for_channel("alpha")
    assert len(alpha) == 2
    assert all(m.channel_id == "alpha" for m in alpha)

    beta = store.get_for_channel("beta")
    assert len(beta) == 1


def test_message_pruning(conn):
    """Messages beyond MAX_MESSAGES are pruned on insert."""
    store = MessageStore(conn)
    from meshcore_console.meshcore.state_store import MAX_MESSAGES

    for i in range(MAX_MESSAGES + 10):
        store.append(
            _make_message(
                msg_id=f"m-{i}",
                created_at=datetime(2025, 1, 1, 0, 0, i % 60, tzinfo=UTC),
            )
        )
    assert len(store) == MAX_MESSAGES


# ---------------------------------------------------------------------------
# PeerStore tests
# ---------------------------------------------------------------------------


def _make_peer(
    peer_id="p1",
    name="Alice",
    signal=80,
    public_key="abc123",
    is_repeater=False,
    rssi=-85,
    snr=7.5,
    lat=None,
    lon=None,
):
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    return Peer(
        peer_id=peer_id,
        display_name=name,
        signal_quality=signal,
        public_key=public_key,
        last_advert_time=now,
        last_path=["hop1", "hop2"],
        is_repeater=is_repeater,
        rssi=rssi,
        snr=snr,
        latitude=lat,
        longitude=lon,
        location_updated=now if lat is not None else None,
    )


def test_peer_round_trip_all_fields(conn):
    """Every Peer field survives add_or_update→get. Catches column order mismatches."""
    store = PeerStore(conn)
    original = _make_peer(
        peer_id="peer-99",
        name="Charlie",
        signal=65,
        public_key="deadbeef" * 8,
        is_repeater=True,
        rssi=-110,
        snr=-3.2,
        lat=47.6,
        lon=-122.3,
    )
    store.add_or_update(original)
    loaded = store.get("Charlie")

    assert loaded is not None
    assert loaded.peer_id == "peer-99"
    assert loaded.display_name == "Charlie"
    assert loaded.signal_quality == 65
    assert loaded.public_key == "deadbeef" * 8
    assert loaded.last_advert_time == original.last_advert_time
    assert loaded.last_path == ["hop1", "hop2"]
    assert loaded.is_repeater is True
    assert loaded.rssi == -110
    assert loaded.snr == -3.2
    assert loaded.latitude == 47.6
    assert loaded.longitude == -122.3
    assert loaded.location_updated == original.location_updated


def test_peer_nullable_fields(conn):
    store = PeerStore(conn)
    peer = Peer(peer_id="p-null", display_name="Null")
    store.add_or_update(peer)
    loaded = store.get("Null")
    assert loaded is not None
    assert loaded.signal_quality is None
    assert loaded.public_key is None
    assert loaded.last_advert_time is None
    assert loaded.last_path == []
    assert loaded.rssi is None
    assert loaded.snr is None
    assert loaded.latitude is None
    assert loaded.longitude is None
    assert loaded.location_updated is None


def test_peer_update_replaces(conn):
    store = PeerStore(conn)
    store.add_or_update(_make_peer(peer_id="p1", name="Alice", signal=80))
    store.add_or_update(Peer(peer_id="p1", display_name="Alice", signal_quality=95))
    loaded = store.get("Alice")
    assert loaded is not None
    assert loaded.signal_quality == 95


def test_peer_get_all(conn):
    store = PeerStore(conn)
    store.add_or_update(_make_peer(peer_id="p1", name="Alice"))
    store.add_or_update(_make_peer(peer_id="p2", name="Bob"))
    all_peers = store.get_all()
    assert len(all_peers) == 2
    assert "Alice" in all_peers
    assert "Bob" in all_peers


def test_peer_get_missing_returns_none(conn):
    store = PeerStore(conn)
    assert store.get("nonexistent") is None


def test_peer_is_repeater_false_round_trip(conn):
    """is_repeater=False must not become True (int 0 → bool)."""
    store = PeerStore(conn)
    store.add_or_update(_make_peer(is_repeater=False))
    loaded = store.get("Alice")
    assert loaded is not None
    assert loaded.is_repeater is False


# ---------------------------------------------------------------------------
# UIChannelStore tests
# ---------------------------------------------------------------------------


def test_channel_round_trip(conn):
    store = UIChannelStore(conn)
    ch = Channel(channel_id="general", display_name="#general", unread_count=5)
    store.add_or_update(ch)
    loaded = store.get("general")
    assert loaded is not None
    assert loaded.channel_id == "general"
    assert loaded.display_name == "#general"
    assert loaded.unread_count == 5


def test_channel_get_missing_returns_none(conn):
    store = UIChannelStore(conn)
    assert store.get("nope") is None


def test_channel_update_replaces(conn):
    store = UIChannelStore(conn)
    store.add_or_update(Channel(channel_id="ch1", display_name="#ch1", unread_count=0))
    store.add_or_update(Channel(channel_id="ch1", display_name="#ch1-renamed", unread_count=3))
    loaded = store.get("ch1")
    assert loaded is not None
    assert loaded.display_name == "#ch1-renamed"
    assert loaded.unread_count == 3


def test_channel_increment_unread(conn):
    store = UIChannelStore(conn)
    store.add_or_update(Channel(channel_id="ch1", display_name="#ch1", unread_count=0))
    store.increment_unread("ch1")
    store.increment_unread("ch1")
    loaded = store.get("ch1")
    assert loaded is not None
    assert loaded.unread_count == 2


def test_channel_get_all(conn):
    store = UIChannelStore(conn)
    store.add_or_update(Channel(channel_id="a", display_name="#a"))
    store.add_or_update(Channel(channel_id="b", display_name="#b"))
    all_channels = store.get_all()
    assert len(all_channels) == 2
    assert "a" in all_channels
    assert "b" in all_channels


# ---------------------------------------------------------------------------
# PacketStore tests
# ---------------------------------------------------------------------------


def test_packet_round_trip(conn):
    store = PacketStore(conn)
    pkt = {"type": "packet", "data": {"rssi": -90}, "received_at": "2025-01-15T12:00:00"}
    store.append(pkt)
    result = store.get_all()
    assert len(result) == 1
    assert result[0]["type"] == "packet"
    assert result[0]["data"]["rssi"] == -90


def test_packet_get_recent(conn):
    store = PacketStore(conn)
    for i in range(20):
        store.append({"type": "pkt", "seq": i, "received_at": f"2025-01-15T12:00:{i:02d}"})

    recent = store.get_recent(5)
    assert len(recent) == 5
    # Should be in chronological order (oldest first)
    assert recent[0]["seq"] == 15
    assert recent[4]["seq"] == 19


def test_packet_get_recent_zero(conn):
    store = PacketStore(conn)
    store.append({"type": "pkt", "received_at": "2025-01-15T12:00:00"})
    assert store.get_recent(0) == []


def test_packet_clear(conn):
    store = PacketStore(conn)
    store.append({"type": "pkt", "received_at": "2025-01-15T12:00:00"})
    assert len(store) == 1
    store.clear()
    assert len(store) == 0
    assert store.get_all() == []


def test_packet_pruning(conn):
    store = PacketStore(conn)
    from meshcore_console.meshcore.packet_store import MAX_PACKETS

    for i in range(MAX_PACKETS + 50):
        store.append({"seq": i, "received_at": f"2025-01-01T00:00:{i % 60:02d}"})
    assert len(store) == MAX_PACKETS


def test_packet_auto_timestamp(conn):
    """Packets without received_at get one auto-assigned."""
    store = PacketStore(conn)
    store.append({"type": "bare"})
    rows = conn.execute("SELECT received_at FROM packets").fetchall()
    assert rows[0][0] is not None
    assert len(rows[0][0]) > 0


# ---------------------------------------------------------------------------
# ChannelDatabase tests
# ---------------------------------------------------------------------------


def test_channel_db_public_channel_auto_created(conn):
    db = ChannelDatabase(conn)
    channels = db.get_channels()
    names = [ch["name"] for ch in channels]
    assert "Public" in names
    pub = db.get_channel("Public")
    assert pub is not None
    assert pub["secret"] == PUBLIC_CHANNEL_SECRET


def test_channel_db_add_and_get(conn):
    db = ChannelDatabase(conn)
    db.add_channel("Emergency", "deadbeef1234")
    ch = db.get_channel("Emergency")
    assert ch is not None
    assert ch["name"] == "Emergency"
    assert ch["secret"] == "deadbeef1234"


def test_channel_db_remove(conn):
    db = ChannelDatabase(conn)
    db.add_channel("Temp", "secret123")
    db.remove_channel("Temp")
    assert db.get_channel("Temp") is None


def test_channel_db_get_missing_returns_none(conn):
    db = ChannelDatabase(conn)
    assert db.get_channel("nonexistent") is None


def test_channel_db_upsert(conn):
    db = ChannelDatabase(conn)
    db.add_channel("Test", "old-secret")
    db.add_channel("Test", "new-secret")
    ch = db.get_channel("Test")
    assert ch is not None
    assert ch["secret"] == "new-secret"


def test_channel_db_get_channels_format(conn):
    """get_channels must return list of dicts with 'name' and 'secret' keys."""
    db = ChannelDatabase(conn)
    db.add_channel("Extra", "abc")
    channels = db.get_channels()
    for ch in channels:
        assert "name" in ch
        assert "secret" in ch
        assert isinstance(ch["name"], str)
        assert isinstance(ch["secret"], str)


# ---------------------------------------------------------------------------
# Cross-store isolation test
# ---------------------------------------------------------------------------


def test_stores_share_connection_without_interference(conn):
    """All stores operating on the same connection don't corrupt each other."""
    settings = SettingsStore(conn)
    messages = MessageStore(conn)
    peers = PeerStore(conn)
    channels = UIChannelStore(conn)
    packets = PacketStore(conn)
    channel_db = ChannelDatabase(conn)

    settings.save(MeshcoreSettings(node_name="multi"))
    messages.append(_make_message(msg_id="cross-1"))
    peers.add_or_update(_make_peer(peer_id="cross-p"))
    channels.add_or_update(Channel(channel_id="cross-c", display_name="#cross"))
    packets.append({"type": "cross", "received_at": "2025-01-01T00:00:00"})
    channel_db.add_channel("CrossChan", "secret")

    assert settings.load().node_name == "multi"
    assert len(messages) == 1
    assert len(peers) == 1
    assert len(channels) == 1
    assert len(packets) == 1
    assert channel_db.get_channel("CrossChan") is not None
