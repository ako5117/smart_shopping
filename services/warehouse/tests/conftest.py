import os
import uuid

import pytest

from warehouse.db import Database
from warehouse.service import Conflict, Unavailable, Warehouse

WH = "WH1"
MILK = "5000000000017"
SUGAR = "5000000000024"

# Set TEST_DATABASE_URL=postgresql://... to run every test against PostgreSQL instead of SQLite.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
_pg_databases = []


def fresh_db() -> Database:
    """An empty database for one test: SQLite in memory, or a throwaway PostgreSQL schema."""
    if TEST_DATABASE_URL:
        db = Database(TEST_DATABASE_URL, schema=f"test_{uuid.uuid4().hex[:12]}")
        _pg_databases.append(db)
        return db
    return Database(":memory:")


@pytest.fixture(scope="session", autouse=True)
def _drop_test_schemas():
    yield
    for db in _pg_databases:
        db.drop_schema()


class FakeInventory:
    """Products by barcode, and restocks into stores, like the Inventory Service."""

    def __init__(self):
        self.products = {MILK: {"product_id": "milk-500ml", "ean13": MILK, "name": "Milk 500ml"},
                         SUGAR: {"product_id": "sugar-1kg", "ean13": SUGAR, "name": "Sugar 1kg"}}
        self.restocks = {}  # scan_id -> (store_id, ean13, qty, by), like the Inventory Service's idempotent restocks
        self.down = False
        self.fail_after = None  # refuse restocks once this many have been made
        self.refuse = False

    def product(self, ean13):
        if self.down:
            raise Unavailable("down")
        return self.products.get(ean13)

    def restock(self, store_id, ean13, qty, scan_id, recorded_by):
        if self.down or (self.fail_after is not None and len(self.restocks) >= self.fail_after):
            raise Unavailable("down")
        if self.refuse:
            raise Conflict("refused")
        self.restocks.setdefault(scan_id, (store_id, ean13, qty, recorded_by))

    def store_stock(self, store_id, ean13):
        return sum(q for s, e, q, _ in self.restocks.values() if s == store_id and e == ean13)


@pytest.fixture
def db():
    return fresh_db()


@pytest.fixture
def inventory():
    return FakeInventory()


@pytest.fixture
def wh(db, inventory):
    return Warehouse(db, inventory, WH)
