import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from notify.db import Database
from notify.service import Notifier, Settings
from notify.sms import SendResult

STORE = "001"
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)

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


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


class FakeSender:
    """Records texts; answers with the queued outcomes in turn, then 'sent'."""

    name = "fake"

    def __init__(self):
        self.sent = []
        self.outcomes = []

    def send(self, to, body):
        self.sent.append((to, body))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            return SendResult(outcome, error="" if outcome == "sent" else f"{outcome} from provider")
        return SendResult("sent", ref=f"REF-{len(self.sent)}", cost="KES 0.8000")


class FakeStores:
    """The Inventory and Dispatch Services' read endpoints the notifier uses."""

    def __init__(self):
        self.sales = []
        self.deliveries = {}
        self.riders = {}
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        path, q = request.url.path, request.url.params
        if path == "/sales":
            assert q["channel"] == "online" and q["status"] == "paid" and q["store_id"] == STORE
            return httpx.Response(200, json=self.sales)
        if path == "/deliveries":
            statuses = q["status"].split(",")
            return httpx.Response(200, json=[{k: v for k, v in d.items() if k != "pin"}
                                             for d in self.deliveries.values() if d["status"] in statuses])
        if path.startswith("/deliveries/"):
            d = self.deliveries.get(path.split("/")[2])
            if not d:
                return httpx.Response(404, json={"detail": "no"})
            return httpx.Response(200, json=d if q.get("include_pin") == "true" else
                                  {k: v for k, v in d.items() if k != "pin"})
        if path.startswith("/riders/"):
            r = self.riders.get(path.split("/")[2])
            return httpx.Response(200, json=r) if r else httpx.Response(404, json={"detail": "no"})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="http://stores", transport=httpx.MockTransport(self.handler))

    def add_sale(self, sale_id="WEB-0000AAAA", phone="0712345678", fulfilment="collect", paid=ago(minutes=1),
                 **kw):
        sale = {"sale_id": sale_id, "store_id": STORE, "status": "paid", "channel": "online",
                "customer_phone": phone, "fulfilment": fulfilment, "updated_at": paid, "ready_at": None,
                "exited_at": None, "delivery_fee_kes": 150 if fulfilment == "delivery" else 0,
                "items": [{"product_id": "MILK", "qty": 2, "unit_price_kes": 65},
                          {"product_id": "BREAD", "qty": 1, "unit_price_kes": 70}], **kw}
        self.sales.append(sale)
        return sale


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def db():
    return fresh_db()


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def stores():
    return FakeStores()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def notifier(db, sender, stores, clock):
    client = stores.client()
    return Notifier(db, sender, Settings(STORE, "Mama Mboga Mart"), client, client, clock=clock)
