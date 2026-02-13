"""SQLite database connection and migration system."""

from __future__ import annotations

import logging
import sqlite3

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
]


def _get_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 if no schema_version table."""
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


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
    conn.execute("UPDATE schema_version SET version = ?", (target,))
    conn.commit()


def open_db(path: str | None = None) -> sqlite3.Connection:
    """Open (and migrate if needed) the application database."""
    db = db_path() if path is None else __import__("pathlib").Path(path)
    db.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False is required because pyMC_core reads the
    # channel_secrets table from the meshcore-aio thread while the connection
    # is created on the main thread.  WAL mode makes concurrent access safe.
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn
