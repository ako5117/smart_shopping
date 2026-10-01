import pytest

from inventory.db import Database
from inventory.service import Inventory

STORE = "001"
FLOUR, SUGAR, MILK = "maize-flour-2kg", "sugar-1kg", "milk-500ml"
EAN = {FLOUR: "6161000000019", SUGAR: "6161000000026", MILK: "6161000000040"}


@pytest.fixture
def db():
    return Database(":memory:")


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
