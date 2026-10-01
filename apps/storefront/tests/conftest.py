import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from storefront.app import create_app
from storefront.config import Settings

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class FakeInventory:
    """Behaves like the Inventory Service for the calls the storefront makes."""

    def __init__(self):
        self.down = False
        self.products = [
            {"product_id": "milk-500ml", "ean13": "6161000000040", "name": "Milk 500 ml", "price_kes": 65, "category": "Dairy"},
            {"product_id": "uht-milk-500ml", "ean13": "6161000000088", "name": "Long-life milk 500 ml", "price_kes": 75, "category": "Dairy"},
            {"product_id": "yoghurt-500ml", "ean13": "6161000000095", "name": "Yoghurt 500 ml", "price_kes": 120, "category": "Dairy"},
            {"product_id": "sugar-1kg", "ean13": "6161000000026", "name": "Sugar 1 kg", "price_kes": 180, "category": "Sugar"},
            {"product_id": "bread-400g", "ean13": "6161000000064", "name": "Bread 400 g", "price_kes": None, "category": "Bakery"},
        ]
        self.stock = {"milk-500ml": 10, "uht-milk-500ml": 0, "yoghurt-500ml": 8, "sugar-1kg": 4}
        self.sales = {}
        self.refuse_online = False  # as if another customer took the last items a moment earlier

    def held(self):
        out = {}
        for s in self.sales.values():
            if s["status"] == "pending_payment":
                for it in s["items"]:
                    out[it["product_id"]] = out.get(it["product_id"], 0) + it["qty"]
        return out

    def availability(self):
        held = self.held()
        return {pid: {"stock": q, "held": held.get(pid, 0)} for pid, q in self.stock.items()}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path, method = request.url.path, request.method
        if path == "/products":
            return httpx.Response(200, json=self.products)
        if path == "/availability/001":
            return httpx.Response(200, json=self.availability())
        if method == "POST" and path == "/sales":
            body = json.loads(request.content)
            if body.get("check_stock") and self.refuse_online:
                short = [{"product_id": it["product_id"], "requested": it["qty"], "available": 0} for it in body["items"]]
                return httpx.Response(409, json={"detail": {"message": "Not enough stock", "short": short}})
            if body.get("check_stock"):
                avail = self.availability()
                short = [{"product_id": it["product_id"], "requested": it["qty"],
                          "available": max(0, avail.get(it["product_id"], {"stock": 0, "held": 0})["stock"]
                                           - avail.get(it["product_id"], {"stock": 0, "held": 0})["held"])}
                         for it in body["items"]]
                short = [s for s in short if s["requested"] > s["available"]]
                if short:
                    return httpx.Response(409, json={"detail": {"message": "Not enough stock", "short": short}})
            prices = {p["product_id"]: p["price_kes"] for p in self.products}
            items = [{**it, "unit_price_kes": prices.get(it["product_id"])} for it in body["items"]]
            self.sales[body["sale_id"]] = {"sale_id": body["sale_id"], "store_id": body["store_id"], "items": items,
                                           "channel": body.get("channel", "in_store"),
                                           "fulfilment": body.get("fulfilment", "collect"),
                                           "delivery_fee_kes": body.get("delivery_fee_kes", 0),
                                           "customer_name": body.get("customer_name", ""),
                                           "status": "pending_payment", "payment_ref": None, "exited_at": None,
                                           "ready_at": None, "created_at": NOW.isoformat()}
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

    def pay(self, sale_id):
        sale = self.sales[sale_id]
        sale["status"] = "paid"
        for it in sale["items"]:
            self.stock[it["product_id"]] -= it["qty"]


class FakePayments:
    def __init__(self, inventory: FakeInventory):
        self.inventory = inventory
        self.payments = {}
        self.pushes = []
        self.fail_push = None

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
                                  "created_at": NOW.isoformat(), "updated_at": NOW.isoformat()}
            return httpx.Response(202, json={"payment_id": pid, "status": "pending"})
        if path.startswith("/payments/"):
            parts = path.split("/")
            p = self.payments.get(parts[2])
            if not p:
                return httpx.Response(404, json={"detail": "Payment not found"})
            return httpx.Response(200, json=p)
        return httpx.Response(404)

    def settle(self, pid, status, receipt=None):
        p = self.payments[pid]
        p.update(status=status, mpesa_receipt_number=receipt)
        if status == "paid":
            self.inventory.pay(p["sale_id"])


class FakeDispatch:
    def __init__(self):
        self.down = False
        self.refuse = False
        self.deliveries = {}
        self.riders = {"otieno": {"rider_id": "otieno", "phone": "0722000111", "on_shift": True}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path, method = request.url.path, request.method
        if path == "/areas":
            return httpx.Response(200, json=[{"area": "Kilimani", "fee_kes": 150}, {"area": "Westlands", "fee_kes": 200}])
        if method == "POST" and path == "/deliveries":
            if self.refuse:
                return httpx.Response(422, json={"detail": "nope"})
            body = json.loads(request.content)
            self.deliveries[body["sale_id"]] = {**body, "status": "awaiting_payment", "fee_kes": 150, "pin": "4821",
                                                "rider_id": None, "picked_up_at": None, "delivered_at": None}
            return httpx.Response(201, json=self.deliveries[body["sale_id"]])
        if path.startswith("/deliveries/"):
            d = self.deliveries.get(path.split("/")[2])
            if not d:
                return httpx.Response(404, json={"detail": "No delivery"})
            out = dict(d)
            if request.url.params.get("include_pin") != "true":
                out.pop("pin")
            return httpx.Response(200, json=out)
        if path.startswith("/riders/"):
            r = self.riders.get(path.split("/")[2])
            return httpx.Response(200, json=r) if r else httpx.Response(404, json={"detail": "No rider"})
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def inventory():
    return FakeInventory()


@pytest.fixture
def dispatch():
    return FakeDispatch()


@pytest.fixture
def payments(inventory):
    return FakePayments(inventory)


@pytest.fixture
def shelf_log(tmp_path):
    return tmp_path / "shelf_events.jsonl"


def shelf_event(log, event_type, product_id, qty=1, minutes_ago=1):
    ts = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event_type": event_type, "zone_id": "B1", "product_id": product_id, "qty": qty, "ts": ts}) + "\n")


@pytest.fixture
def clock():
    return {"now": NOW}


@pytest.fixture
def client(inventory, payments, dispatch, shelf_log, clock):
    settings = Settings(store_id="001", store_name="Test Store", inventory_url="http://inv", payments_url="http://pay",
                        shelf_events_path=str(shelf_log), in_store_buffer=1, few_left=5,
                        products_cache_s=0, stock_cache_s=0)
    return TestClient(create_app(
        settings,
        inventory=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(inventory.handler)),
        payments=httpx.Client(base_url="http://pay", transport=httpx.MockTransport(payments.handler)),
        clock=lambda: clock["now"],
        dispatch=httpx.Client(base_url="http://dispatch", transport=httpx.MockTransport(dispatch.handler)),
    ))
