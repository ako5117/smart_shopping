"""Customer accounts and loyalty points. Stored in SQLite (development, tests) or the shared PostgreSQL
database (see sqldb.py), in schema "customers".

Personal details stay in this service. The online shop asks for them only for the signed-in customer,
and the dashboard sees names, join dates and points, never dates of birth or gender.
"""

import os

from .sqldb import Database as _Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,                       -- 2547..., unique among live accounts
    email TEXT NOT NULL UNIQUE,       -- lower case
    dob TEXT,                         -- YYYY-MM-DD
    gender TEXT,                      -- female | male | other | NULL (not given)
    consent_at TEXT NOT NULL,         -- when they accepted the privacy notice
    consent_version TEXT NOT NULL,
    email_verified_at TEXT,           -- NULL until they enter the emailed code
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT                   -- account deleted: personal details erased, points history kept
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);

CREATE TABLE IF NOT EXISTS customer_photos (
    customer_id TEXT PRIMARY KEY REFERENCES customers(customer_id),
    data BLOB NOT NULL,               -- JPEG, at most 400 x 400
    updated_at TEXT NOT NULL
);

-- One-time codes sent by email: to confirm the address at sign-up, and to sign in.
CREATE TABLE IF NOT EXISTS codes (
    code_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    salt TEXT NOT NULL,
    code_hash TEXT NOT NULL,          -- the code itself is never stored
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_codes_email ON codes(email, created_at);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,      -- sha256 of the cookie value
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- Online orders placed while signed in: points are earned once the order is paid.
CREATE TABLE IF NOT EXISTS orders (
    sale_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'cancelled')),
    redeemed_points INTEGER NOT NULL DEFAULT 0,
    discount_kes INTEGER NOT NULL DEFAULT 0,
    earned_points INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    settled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_pending ON orders(status);

-- Every change to a balance. The balance is the sum; each order earns, spends or gets points back once.
CREATE TABLE IF NOT EXISTS points (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    sale_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('earn', 'redeem', 'refund')),
    points INTEGER NOT NULL,          -- + earned or given back, - spent
    created_at TEXT NOT NULL,
    UNIQUE (sale_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_points_customer ON points(customer_id);

-- Problems and bugs reported by customers (online shop or Scan & Go), answered by staff on the dashboard.
CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('order', 'store', 'app', 'idea')),
    message TEXT NOT NULL,
    app TEXT NOT NULL,                -- store (online shop) | scan_and_go
    order_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT,                 -- signed in when they reported
    contact TEXT NOT NULL DEFAULT '', -- phone or email to reach them, if they gave one
    context TEXT NOT NULL DEFAULT '', -- for app problems: page, browser, recent errors (JSON)
    status TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'resolved')),
    reply TEXT NOT NULL DEFAULT '',   -- what staff tell the customer
    handled_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status, created_at);
CREATE INDEX IF NOT EXISTS idx_reports_customer ON reports(customer_id);
CREATE TABLE IF NOT EXISTS report_photos (
    report_id TEXT PRIMARY KEY REFERENCES reports(report_id),
    data BLOB NOT NULL
);

-- Stand-in email: messages are kept here (and shown on the dashboard) instead of being sent.
CREATE TABLE IF NOT EXISTS emails (
    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    provider TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

DEFAULT_SQLITE_PATH = "customers.db"


class Database(_Database):
    """The Customers Service's database: a SQLite path, or a postgresql:// URL (tables in schema "customers")."""

    def __init__(self, url: str, schema: str = "customers"):
        super().__init__(url, SCHEMA, schema=schema)


def database_from_env() -> Database:
    """DATABASE_URL (postgresql://...) when set, otherwise the SQLite file CUSTOMERS_DB_PATH."""
    url = os.getenv("DATABASE_URL") or os.getenv("CUSTOMERS_DB_PATH", DEFAULT_SQLITE_PATH)
    return Database(url, schema=os.getenv("DATABASE_SCHEMA", "customers"))
