import os
import uuid

import pytest

from inventory.db import Database
from inventory.service import Inventory

STORE = "001"
FLOUR, SUGAR, MILK = "maize-flour-2kg", "sugar-1kg", "milk-500ml"
EAN = {FLOUR: "6161000000019", SUGAR: "6161000000026", MILK: "6161000000040"}


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


@pytest.fixture
def db():
    return fresh_db()


@pytest.fixture
def inv(db):
    i = Inventory(db)
    for pid, ean in EAN.items():
        i.upsert_product(pid, ean, pid)
    return i


class FakeRetailer:
    def __init__(self):
        self.received = []
        self.fail_next = 0
        self.stock = {}

    def send(self, movement):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("retailer offline")
        if movement["idempotency_key"] not in {m["idempotency_key"] for m in self.received}:
            self.received.append(movement)

    def stock_snapshot(self, store_id):
        return dict(self.stock)


@pytest.fixture
def retailer():
    return FakeRetailer()
