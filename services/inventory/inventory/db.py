"""SQLite storage. Swapped for the shared database once it is set up."""

import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    ean13 TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL
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
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_stock ON stock_ledger(store_id, product_id);
CREATE TABLE IF NOT EXISTS sales (
    sale_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_payment', 'paid', 'cancelled')),
    payment_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sale_items (
    sale_id TEXT NOT NULL REFERENCES sales(sale_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    qty INTEGER NOT NULL CHECK (qty > 0),
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


class Database:
    def __init__(self, path: str):
        # One shared connection keeps ":memory:" databases alive for tests.
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        """All-or-nothing: a sale's ledger entries and outbox rows are written together or not at all."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def one(self, sql, args=()):
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]
