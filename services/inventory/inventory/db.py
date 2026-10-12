"""Inventory tables. Stored in SQLite (development, tests) or the shared PostgreSQL database (see sqldb.py)."""

# Columns added after the first release; ALTER TABLE brings older database files up to date.
ADDED_COLUMNS = {
    "products": [("price_kes", "INTEGER CHECK (price_kes IS NULL OR price_kes > 0)"),
                 ("category", "TEXT NOT NULL DEFAULT ''"), ("perishable", "INTEGER NOT NULL DEFAULT 0")],
    "sales": [("exited_at", "TEXT"), ("exited_by", "TEXT"),
              ("channel", "TEXT NOT NULL DEFAULT 'in_store'"), ("customer_name", "TEXT NOT NULL DEFAULT ''"),
              ("ready_at", "TEXT"), ("ready_by", "TEXT"),
              ("fulfilment", "TEXT NOT NULL DEFAULT 'collect'"), ("delivery_fee_kes", "INTEGER NOT NULL DEFAULT 0"),
              ("customer_phone", "TEXT NOT NULL DEFAULT ''"),
              ("loyalty_discount_kes", "INTEGER NOT NULL DEFAULT 0"), ("customer_id", "TEXT NOT NULL DEFAULT ''"),
              ("paid_at", "TEXT"), ("traveller", "TEXT NOT NULL DEFAULT ''")],
    "sale_items": [("unit_price_kes", "INTEGER"), ("list_price_kes", "INTEGER")],
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
    category TEXT NOT NULL DEFAULT '',
    perishable INTEGER NOT NULL DEFAULT 0  -- food that goes off: never taken back (see returns)
);
CREATE TABLE IF NOT EXISTS product_images (  -- one photo per product, as shown in the shops (inventory/images.py)
    product_id TEXT PRIMARY KEY REFERENCES products(product_id),
    data BLOB NOT NULL,  -- JPEG
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    updated_at TEXT NOT NULL,  -- also the photo's version, so browsers can cache it
    updated_by TEXT NOT NULL DEFAULT ''
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
CREATE TABLE IF NOT EXISTS stock_counts (  -- staff counted a product; any difference is an adjustment in the ledger
    count_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    counted_qty INTEGER NOT NULL CHECK (counted_qty >= 0),
    was_qty INTEGER NOT NULL,  -- what the ledger said just before
    counted_by TEXT NOT NULL DEFAULT '',
    counted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_counts_product ON stock_counts(store_id, product_id, counted_at);
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
    customer_phone TEXT NOT NULL DEFAULT '',  -- online orders: for order updates by SMS
    loyalty_discount_kes INTEGER NOT NULL DEFAULT 0,  -- online orders: taken off for loyalty points spent
    customer_id TEXT NOT NULL DEFAULT '',  -- online orders by a signed-in customer (Customers Service)
    paid_at TEXT,  -- when the payment was confirmed (the returns window starts here)
    traveller TEXT NOT NULL DEFAULT ''  -- airport shops: who flew where (JSON: name, flight, from, to, date, seat), no booking reference
);
CREATE TABLE IF NOT EXISTS sale_items (
    sale_id TEXT NOT NULL REFERENCES sales(sale_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price_kes INTEGER,  -- what the customer paid each, after any discount
    list_price_kes INTEGER,  -- the shelf price at the time, before the discount
    PRIMARY KEY (sale_id, product_id)
);
CREATE TABLE IF NOT EXISTS discounts (  -- set by managers, per product: a percentage or an amount off
    discount_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    kind TEXT NOT NULL CHECK (kind IN ('percent', 'amount')),
    value INTEGER NOT NULL CHECK (value > 0),
    starts_at TEXT NOT NULL,
    ends_at TEXT,  -- NULL: until a manager ends it
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    ended_by TEXT,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_discounts_product ON discounts(product_id, starts_at);
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

-- Returns (see returns.py): asked for by the customer online or started by staff at the counter,
-- checked and approved by a manager (stock goes back), then refunded by hand.
CREATE TABLE IF NOT EXISTS returns (
    return_id TEXT PRIMARY KEY,
    sale_id TEXT NOT NULL REFERENCES sales(sale_id),
    store_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('requested', 'approved', 'rejected', 'refunded')),
    reason TEXT NOT NULL,
    started_by TEXT NOT NULL,          -- "customer" (online) or the staff member
    created_at TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    note TEXT NOT NULL DEFAULT '',     -- why it was rejected, or what was checked
    refund_kes INTEGER NOT NULL DEFAULT 0,
    refund_method TEXT,                -- mpesa | card | cash
    refund_ref TEXT,
    refunded_by TEXT,
    refunded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_returns_status ON returns(store_id, status);
CREATE INDEX IF NOT EXISTS idx_returns_sale ON returns(sale_id);
CREATE TABLE IF NOT EXISTS return_lines (
    return_id TEXT NOT NULL REFERENCES returns(return_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    refund_kes INTEGER NOT NULL,       -- for the whole line: what was paid for these items
    PRIMARY KEY (return_id, product_id)
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
