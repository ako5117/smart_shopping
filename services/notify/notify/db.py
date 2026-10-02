"""Notification tables. Stored in SQLite (development, tests) or the shared PostgreSQL database (see sqldb.py)."""

import os

from .sqldb import Database as _Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT UNIQUE NOT NULL,  -- kind:sale_id, so each update is texted once
    kind TEXT NOT NULL,              -- paid | ready | on_the_way
    sale_id TEXT NOT NULL,
    to_number TEXT NOT NULL,         -- +2547...
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    provider_ref TEXT,
    cost TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_due ON messages(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_messages_sale ON messages(sale_id);
"""

DEFAULT_SQLITE_PATH = "notify.db"


class Database(_Database):
    """The Notification Service's database: a SQLite path, or a postgresql:// URL (tables in schema "notify")."""

    def __init__(self, url: str, schema: str = "notify"):
        super().__init__(url, SCHEMA, schema=schema)


def database_from_env() -> Database:
    """DATABASE_URL (postgresql://...) when set, otherwise the SQLite file NOTIFY_DB_PATH."""
    url = os.getenv("DATABASE_URL") or os.getenv("NOTIFY_DB_PATH", DEFAULT_SQLITE_PATH)
    return Database(url, schema=os.getenv("DATABASE_SCHEMA", "notify"))
