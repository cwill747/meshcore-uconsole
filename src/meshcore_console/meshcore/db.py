"""SQLite database connection and migration system."""

from __future__ import annotations

import hashlib
import logging
import sqlite3

from .channel_db import PUBLIC_CHANNEL_SECRET
from .paths import db_path

logger = logging.getLogger(__name__)

SCHEMA_V1 = """\
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);

INSERT INTO schema_version (version) VALUES (1);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE channels (
    channel_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    unread_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE channel_secrets (
    name TEXT PRIMARY KEY,
    secret TEXT NOT NULL
);

CREATE TABLE peers (
    peer_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    signal_quality INTEGER,
    public_key TEXT,
    last_advert_time TEXT,
    last_path TEXT,
    is_repeater INTEGER NOT NULL DEFAULT 0,
    rssi INTEGER,
    snr REAL,
    latitude REAL,
    longitude REAL,
    location_updated TEXT
);

CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    sender_id TEXT NOT NULL,
    body TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT 'public',
    created_at TEXT NOT NULL,
    is_outgoing INTEGER NOT NULL DEFAULT 0,
    path_len INTEGER NOT NULL DEFAULT 0,
    snr REAL,
    rssi INTEGER
);

CREATE TABLE packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    data TEXT NOT NULL
);
"""

# Each migration takes the database from version N to N+1.
# Index 0 = v0 -> v1 (initial schema creation).
MIGRATIONS: list[tuple[str, ...]] = [
    tuple(stmt.strip() for stmt in SCHEMA_V1.split(";") if stmt.strip()),
    # v1 -> v2: add peer_name column to channels for original-case contact names
    ("ALTER TABLE channels ADD COLUMN peer_name TEXT",),
    # v2 -> v3: add path_hops column to messages for route visualization
    ("ALTER TABLE messages ADD COLUMN path_hops TEXT",),
    # v3 -> v4: add is_favorite column to peers for user-pinned contacts/repeaters
    ("ALTER TABLE peers ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0",),
    # v4 -> v5: add kind column to channels ("group" or "dm")
    (
        "ALTER TABLE channels ADD COLUMN kind TEXT NOT NULL DEFAULT 'group'",
        # Backfill: channels with a peer_name are DM channels
        "UPDATE channels SET kind = 'dm' WHERE peer_name IS NOT NULL",
    ),
    # v5 -> v6: add repeater_passwords table
    (
        """CREATE TABLE IF NOT EXISTS repeater_passwords (
            peer_name TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
    ),
    # v6 -> v7: backfill channel_secrets for group channels added before the
    # fix for issue #81. The secret is sha256("#" + name)[:32], which cannot be
    # expressed in SQL, so the work happens in _backfill_channel_secrets();
    # this entry exists only to bump the schema version.
    (),
]


def _get_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 if no schema_version table."""
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def _backfill_channel_secrets(conn: sqlite3.Connection) -> None:
    """Derive missing secrets for group channels (issue #81).

    Hashtag channels added before the fix have a row in `channels` but none in
    `channel_secrets`, so sending to them failed with "not in provided
    channels_config". Derive the secret for any such channel.

    Public is special: its secret is a fixed constant shared by all MeshCore
    devices, *not* the derived sha256("#Public")[:32]. It is normally seeded by
    ChannelDatabase.__init__, but that runs after open_db() -> _migrate(), so we
    cannot assume it is present yet. Seed it here and exclude it from derivation;
    otherwise the backfill would mint a bogus derived secret for #public and
    break decryption on the default channel.

    Idempotent: channels that already have a secret (matched case-insensitively)
    are left untouched.
    """
    # Seed Public up-front so it is never treated as an orphan needing derivation.
    conn.execute(
        "INSERT OR IGNORE INTO channel_secrets (name, secret) VALUES (?, ?)",
        ("Public", PUBLIC_CHANNEL_SECRET),
    )

    # LOWER() on both sides: SQLite binds COLLATE NOCASE to a column, not to an
    # expression like LTRIM(...), so relying on it here would silently compare
    # case-sensitively.
    rows = conn.execute(
        """SELECT c.channel_id, c.display_name FROM channels c
           WHERE c.kind = 'group'
             AND LOWER(c.channel_id) != 'public'
             AND NOT EXISTS (
                 SELECT 1 FROM channel_secrets s
                 WHERE LOWER(s.name) = LOWER(c.channel_id)
                    OR LOWER(s.name) = LOWER(LTRIM(c.display_name, '#'))
             )"""
    ).fetchall()
    for channel_id, display_name in rows:
        # Lowercase: the derived secret is keyed on the lowercased name (see
        # derive_channel_secret), so the stored name must match, or peers using
        # the same channel would end up with a different key.
        name = ((display_name or "").lstrip("#") or channel_id).strip().lower()
        secret = hashlib.sha256(f"#{name}".encode()).hexdigest()[:32]
        conn.execute(
            "INSERT OR IGNORE INTO channel_secrets (name, secret) VALUES (?, ?)",
            (name, secret),
        )
        logger.info("Backfilled channel secret for #%s (issue #81)", name)


def _migrate(conn: sqlite3.Connection) -> None:
    """Run any outstanding migrations."""
    current = _get_version(conn)
    target = len(MIGRATIONS)
    if current >= target:
        return
    logger.info("Migrating database from v%d to v%d", current, target)
    for version_index in range(current, target):
        for stmt in MIGRATIONS[version_index]:
            conn.execute(stmt)
    # Data backfills run after all DDL so the columns they read (e.g. channels.kind,
    # added in v5) are guaranteed to exist.
    if current < 7:
        _backfill_channel_secrets(conn)
    conn.execute("UPDATE schema_version SET version = ?", (target,))
    conn.commit()


def open_db(path: str | None = None) -> sqlite3.Connection:
    """Open (and migrate if needed) the application database."""
    db = db_path() if path is None else __import__("pathlib").Path(path)
    db.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False is required because openhop_core reads the
    # channel_secrets table from the meshcore-aio thread while the connection
    # is created on the main thread.  WAL mode makes concurrent access safe.
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn
