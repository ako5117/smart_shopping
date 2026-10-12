"""Warehouse tables. Stored in SQLite (development, tests) or the shared PostgreSQL database (see sqldb.py)."""

import os

from .sqldb import Database as _Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (  -- names and barcodes, copied from the Inventory Service when first scanned
    product_id TEXT PRIMARY KEY,
    ean13 TEXT NOT NULL,
    name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_ean ON products(ean13);
-- Committed warehouse stock, per bin. Stock in a bin is the sum of its entries, never a number edited directly.
CREATE TABLE IF NOT EXISTS ledger (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id TEXT NOT NULL,
    bin TEXT NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    entry_type TEXT NOT NULL CHECK (entry_type IN ('receive', 'move', 'transfer', 'count')),
    qty_change INTEGER NOT NULL,
    ref TEXT NOT NULL DEFAULT '',  -- supplier delivery note, move, transfer or count id
    idempotency_key TEXT UNIQUE NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ledger_bin ON ledger(warehouse_id, product_id, bin);
CREATE TABLE IF NOT EXISTS counts (  -- a stock controller counted a product in a bin; any difference is a ledger entry
    count_id TEXT PRIMARY KEY,
    warehouse_id TEXT NOT NULL,
    bin TEXT NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    counted_qty INTEGER NOT NULL CHECK (counted_qty >= 0),
    was_qty INTEGER NOT NULL,  -- what the ledger said just before
    counted_by TEXT NOT NULL DEFAULT '',
    counted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transfers (  -- goods sent from the warehouse to a store
    transfer_id TEXT PRIMARY KEY,
    warehouse_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('dispatched', 'received')),
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    received_by TEXT,
    received_at TEXT
);
CREATE TABLE IF NOT EXISTS transfer_lines (
    transfer_id TEXT NOT NULL REFERENCES transfers(transfer_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    bin TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    PRIMARY KEY (transfer_id, product_id, bin)
);
"""

DEFAULT_SQLITE_PATH = "warehouse.db"


class Database(_Database):
    """The Warehouse Service's database: a SQLite path, or a postgresql:// URL (tables in schema "warehouse")."""

    def __init__(self, url: str, schema: str = "warehouse"):
        super().__init__(url, SCHEMA, schema=schema)


def database_from_env() -> Database:
    """DATABASE_URL (postgresql://...) when set, otherwise the SQLite file WAREHOUSE_DB_PATH."""
    url = os.getenv("DATABASE_URL") or os.getenv("WAREHOUSE_DB_PATH", DEFAULT_SQLITE_PATH)
    return Database(url, schema=os.getenv("DATABASE_SCHEMA", "warehouse"))
