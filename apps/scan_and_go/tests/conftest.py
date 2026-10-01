import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from scan_and_go.app import create_app
from scan_and_go.config import Settings

MILK, SUGAR, BREAD = "6161000000040", "6161000000026", "6161000000064"


class FakeInventory:
    def __init__(self):
        self.down = False
        self.products = [
            {"product_id": "milk-500ml", "ean13": MILK, "name": "Milk 500 ml", "price_kes": 65},
            {"product_id": "sugar-1kg", "ean13": SUGAR, "name": "Sugar 1 kg", "price_kes": 180},
            {"product_id": "bread-400g", "ean13": BREAD, "name": "Bread 400 g", "price_kes": None},
        ]
        self.sales = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path, method = request.url.path, request.method
        if path == "/products":
            return httpx.Response(200, json=self.products)
        if method == "POST" and path == "/sales":
            body = json.loads(request.content)
            prices = {p["product_id"]: p["price_kes"] for p in self.products}
            items = [{**it, "unit_price_kes": prices.get(it["product_id"])} for it in body["items"]]
            self.sales[body["sale_id"]] = {**body, "items": items, "status": "pending_payment", "payment_ref": None,
                                           "exited_at": None}
            return httpx.Response(201, json=self.sales[body["sale_id"]])
        if path.startswith("/sales/"):
            parts = path.split("/")
            sale = self.sales.get(parts[2])
            if not sale:
                return httpx.Response(404, json={"detail": "No sale"})
            if method == "POST" and parts[3] == "cancel":
                sale["status"] = "cancelled"
            return httpx.Response(200, json=sale)
        return httpx.Response(404, json={"detail": "not found"})


class FakePayments:
    def __init__(self, inventory: FakeInventory):
        self.inventory = inventory
        self.payments = {}
        self.pushes = []
        self.refreshes = 0
        self.fail_push = None  # status code to return from STK push

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if method == "POST" and path == "/payments/mpesa/stk-push":
            body = json.loads(request.content)
            self.pushes.append(body)
            if self.fail_push:
                return httpx.Response(self.fail_push, json={"detail": "nope"})
            pid = f"pay-{len(self.pushes)}"
            self.payments[pid] = {"payment_id": pid, "sale_id": body["sale_id"], "status": "pending",
                                  "amount": body["amount"], "phone_number": "254712345678",
                                  "mpesa_receipt_number": None, "result_desc": None,
                                  "created_at": datetime.now(timezone.utc).isoformat(),
                                  "updated_at": datetime.now(timezone.utc).isoformat()}
            return httpx.Response(202, json={"payment_id": pid, "status": "pending",
                                             "customer_message": "Success. Request accepted for processing"})
        if path.startswith("/payments/"):
            parts = path.split("/")
            p = self.payments.get(parts[2])
            if not p:
                return httpx.Response(404, json={"detail": "Payment not found"})
            if method == "POST" and parts[3] == "refresh":
                self.refreshes += 1
            return httpx.Response(200, json=p)
        return httpx.Response(404)

    def settle(self, pid, status, receipt=None, desc=""):
        p = self.payments[pid]
        p.update(status=status, mpesa_receipt_number=receipt, result_desc=desc)
        if status == "paid":
            self.inventory.sales[p["sale_id"]].update(status="paid", payment_ref=receipt)

    def age(self, pid, seconds):
        self.payments[pid]["created_at"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def inventory():
    return FakeInventory()


@pytest.fixture
def payments(inventory):
    return FakePayments(inventory)


@pytest.fixture
def client(inventory, payments):
    settings = Settings(store_id="001", store_name="Test Store", inventory_url="http://inv", payments_url="http://pay",
                        stk_limit_per_phone=3)
    return TestClient(create_app(
        settings,
        inventory=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(inventory.handler)),
        payments=httpx.Client(base_url="http://pay", transport=httpx.MockTransport(payments.handler)),
    ))
