import os
import re
import uuid
from datetime import datetime, timezone

import pytest

from customers.db import Database
from customers.mailer import OutboxMailer
from customers.service import Customers, Settings

NOW = datetime(2026, 10, 12, 7, 0, tzinfo=timezone.utc)

# Set TEST_DATABASE_URL=postgresql://... to run every test against PostgreSQL instead of SQLite.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
_pg_databases = []


def fresh_db() -> Database:
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
    def __init__(self):
        self.sales = {}
        self.down = False

    def sale(self, sale_id):
        if self.down:
            raise ConnectionError("down")
        return self.sales.get(sale_id)

    def add(self, sale_id, status="paid", items=((250, 2),), fee=0, discount=0):
        self.sales[sale_id] = {"sale_id": sale_id, "status": status, "delivery_fee_kes": fee, "loyalty_discount_kes": discount,
                               "items": [{"product_id": f"p{i}", "unit_price_kes": p, "qty": q} for i, (p, q) in enumerate(items)]}


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def db():
    return fresh_db()


@pytest.fixture
def inventory():
    return FakeInventory()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def customers(db, inventory, clock):
    return Customers(db, OutboxMailer(db), Settings(store_name="Test Store"), inventory, clock)


JANE = {"name": "Jane Wanjiru", "phone": "0712 345 678", "email": "Jane@Example.com", "dob": "1994-05-17",
        "gender": "female", "consent": True}


def last_code(db, email="jane@example.com") -> str:
    row = db.one("SELECT body FROM emails WHERE to_email = ? ORDER BY email_id DESC LIMIT 1", (email,))
    return re.search(r"\b(\d{6})\b", row["body"]).group(1)


def signed_up(customers, db, **kw) -> dict:
    """Jane signs up and enters the code: {token, customer}."""
    details = {**JANE, **kw}
    customers.signup(**details)
    email = details["email"].lower()
    return customers.verify(email, last_code(db, email))
