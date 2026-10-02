"""Inventory tables. Stored in SQLite (development, tests) or the shared PostgreSQL database (see sqldb.py)."""

# Columns added after the first release; ALTER TABLE brings older database files up to date.
ADDED_COLUMNS = {
    "products": [("price_kes", "INTEGER CHECK (price_kes IS NULL OR price_kes > 0)"),
                 ("category", "TEXT NOT NULL DEFAULT ''")],
    "sales": [("exited_at", "TEXT"), ("exited_by", "TEXT"),
              ("channel", "TEXT NOT NULL DEFAULT 'in_store'"), ("customer_name", "TEXT NOT NULL DEFAULT ''"),
              ("ready_at", "TEXT"), ("ready_by", "TEXT"),
              ("fulfilment", "TEXT NOT NULL DEFAULT 'collect'"), ("delivery_fee_kes", "INTEGER NOT NULL DEFAULT 0"),
              ("customer_phone", "TEXT NOT NULL DEFAULT ''")],
    "sale_items": [("unit_price_kes", "INTEGER")],
    "stock_ledger": [("recorded_by", "TEXT NOT NULL DEFAULT ''")],
}

import os

from .sqldb import Database as _Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    ean13 TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    price_kes INTEGER CHECK (price_kes IS NULL OR price_kes > 0),
    category TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS stock_ledger (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    entry_type TEXT NOT NULL CHECK (entry_type IN ('restock', 'sale', 'return', 'adjustment')),
    qty_change INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT UNIQUE NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ledger_stock ON stock_ledger(store_id, product_id);
CREATE TABLE IF NOT EXISTS sales (
    sale_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_payment', 'paid', 'cancelled')),
    payment_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    exited_at TEXT,
    exited_by TEXT,
    channel TEXT NOT NULL DEFAULT 'in_store',  -- in_store (Scan & Go) or online (storefront, click & collect)
    customer_name TEXT NOT NULL DEFAULT '',
    ready_at TEXT,  -- online orders: packed and waiting for collection or a rider
    ready_by TEXT,
    fulfilment TEXT NOT NULL DEFAULT 'collect',  -- online orders: collect (at the store) or delivery (by a rider)
    delivery_fee_kes INTEGER NOT NULL DEFAULT 0,
    customer_phone TEXT NOT NULL DEFAULT ''  -- online orders: for order updates by SMS
);
CREATE TABLE IF NOT EXISTS sale_items (
    sale_id TEXT NOT NULL REFERENCES sales(sale_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price_kes INTEGER,
    PRIMARY KEY (sale_id, product_id)
);
CREATE TABLE IF NOT EXISTS outbox (
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER UNIQUE NOT NULL REFERENCES stock_ledger(entry_id),
    status TEXT NOT NULL CHECK (status IN ('queued', 'confirmed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS price_changes (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    old_price_kes INTEGER,
    new_price_kes INTEGER NOT NULL,
    changed_by TEXT NOT NULL DEFAULT '',
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discrepancies (
    discrepancy_id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    our_qty INTEGER NOT NULL,
    retailer_qty INTEGER NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('timing', 'unknown')),
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
    resolution TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
"""


DEFAULT_SQLITE_PATH = "inventory.db"


class Database(_Database):
    """The Inventory Service's database: a SQLite path, or a postgresql:// URL (tables in schema "inventory")."""

    def __init__(self, url: str, schema: str = "inventory"):
        super().__init__(url, SCHEMA, schema=schema, added_columns=ADDED_COLUMNS)


def database_from_env() -> Database:
    """DATABASE_URL (postgresql://...) when set, otherwise the SQLite file INVENTORY_DB_PATH."""
    url = os.getenv("DATABASE_URL") or os.getenv("INVENTORY_DB_PATH", DEFAULT_SQLITE_PATH)
    return Database(url, schema=os.getenv("DATABASE_SCHEMA", "inventory"))
