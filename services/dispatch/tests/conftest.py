import os
import uuid

import pytest

from dispatch.db import Database
from dispatch.service import Dispatch, parse_areas

STORE = "001"
AREAS = parse_areas("Kilimani:150,Westlands:200")

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
    """Sales by id with a status; records exits like the Inventory Service."""

    def __init__(self):
        self.sales = {}
        self.exits = []
        self.down = False

    def get_sale(self, sale_id):
        return None if self.down else self.sales.get(sale_id)

    def record_exit(self, sale_id, checked_by):
        sale = self.sales.get(sale_id)
        if self.down:
            return False, "The Inventory Service is unavailable"
        if not sale or sale["status"] != "paid":
            return False, f"Order {sale_id} is not paid"
        if sale.get("exited_at"):
            return False, f"Order {sale_id} already left the store"
        sale["exited_at"] = "now"
        self.exits.append((sale_id, checked_by))
        return True, ""


@pytest.fixture
def db():
    return fresh_db()


@pytest.fixture
def inventory():
    return FakeInventory()


@pytest.fixture
def dispatch(db, inventory):
    return Dispatch(db, inventory, AREAS)


def new_delivery(dispatch, inventory, sale_id="WEB-1", paid=True, **kw):
    inventory.sales[sale_id] = {"sale_id": sale_id, "status": "paid" if paid else "pending_payment"}
    args = {"customer_name": "Jane", "customer_phone": "0712345678", "area": "Kilimani",
            "address": "Hse 4, Argwings Kodhek Rd", "notes": "Blue gate", **kw}
    return dispatch.create(sale_id, STORE, **args)
