"""Dispatch tables. Stored in SQLite (development, tests) or the shared PostgreSQL database (see sqldb.py)."""

import os

from .sqldb import Database as _Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS riders (
    rider_id TEXT PRIMARY KEY,  -- the rider's login name
    phone TEXT NOT NULL DEFAULT '',
    on_shift INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    sale_id TEXT PRIMARY KEY,  -- the online order in the Inventory Service
    store_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('awaiting_payment', 'unassigned', 'assigned', 'picked_up',
                                           'delivered', 'failed', 'cancelled')),
    customer_name TEXT NOT NULL,
    customer_phone TEXT NOT NULL,
    area TEXT NOT NULL,
    address TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    lat REAL,
    lng REAL,
    fee_kes INTEGER NOT NULL,
    pin TEXT NOT NULL,  -- the customer gives it to the rider at the door
    pin_attempts INTEGER NOT NULL DEFAULT 0,
    rider_id TEXT REFERENCES riders(rider_id),
    assigned_at TEXT,
    picked_up_at TEXT,
    handed_over_by TEXT,
    delivered_at TEXT,
    failed_at TEXT,
    fail_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(store_id, status);
CREATE TABLE IF NOT EXISTS delivery_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id TEXT NOT NULL REFERENCES deliveries(sale_id),
    what TEXT NOT NULL,
    who TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
);
"""

DEFAULT_SQLITE_PATH = "dispatch.db"


class Database(_Database):
    """The Dispatch Service's database: a SQLite path, or a postgresql:// URL (tables in schema "dispatch")."""

    def __init__(self, url: str, schema: str = "dispatch"):
        super().__init__(url, SCHEMA, schema=schema)


def database_from_env() -> Database:
    """DATABASE_URL (postgresql://...) when set, otherwise the SQLite file DISPATCH_DB_PATH."""
    url = os.getenv("DATABASE_URL") or os.getenv("DISPATCH_DB_PATH", DEFAULT_SQLITE_PATH)
    return Database(url, schema=os.getenv("DATABASE_SCHEMA", "dispatch"))
