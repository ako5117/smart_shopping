import json

import httpx
import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.config import Settings
from dashboard.sources import DispatchClient, InventoryClient

CATALOGUE = {
    "products": [
        {"product_id": "sugar-1kg", "ean13": "6161000000026", "name": "Sugar 1 kg"},
        {"product_id": "milk-500ml", "ean13": "6161000000040", "name": "Milk 500 ml"},
    ],
    "zones": [
        {"zone_id": "A2", "shelf_id": "A", "product_ids": ["sugar-1kg"]},
        {"zone_id": "B1", "shelf_id": "B", "product_ids": ["milk-500ml"]},
    ],
}


def event(event_type, zone_id, product_id, qty=1, ts="2026-10-01T09:00:00+00:00", **extra):
    return {"event_type": event_type, "zone_id": zone_id, "product_id": product_id, "qty": qty,
            "weight_delta_g": -1010.0 * qty, "confidence": 0.9, "source_event_id": "e", "ts": ts,
            "from_zone_id": None, "alert": False, "needs_review": False, "candidates": [], "note": "", **extra}


class FakeInventory:
    """Canned Inventory Service responses; set `down = True` to simulate it being unreachable."""

    def __init__(self):
        self.down = False
        self.counts = []
        self.stock_counts = []  # POST /counts bodies
        self.restocks = []
        self.products = [{"product_id": "sugar-1kg", "ean13": "6161000000026", "name": "Sugar 1 kg"},
                         {"product_id": "milk-500ml", "ean13": "6161000000040", "name": "Milk 500 ml"}]
        self.stock = {"sugar-1kg": 3, "milk-500ml": 20}
        self.sales = [
            {"sale_id": "S3", "store_id": "001", "status": "pending_payment", "payment_ref": None,
             "created_at": "2026-10-01T10:00:00+00:00", "items": [{"product_id": "milk-500ml", "qty": 1}]},
            {"sale_id": "S2", "store_id": "001", "status": "paid", "payment_ref": "REF2",
             "created_at": "2026-10-01T08:00:00+00:00", "items": [{"product_id": "milk-500ml", "qty": 2},
                                                                   {"product_id": "sugar-1kg", "qty": 1}]},
            # 23:00 UTC on 30 Sep is 02:00 on 1 Oct in Nairobi, so this counts as today
            {"sale_id": "S1", "store_id": "001", "status": "paid", "payment_ref": "REF1",
             "created_at": "2026-09-30T23:00:00+00:00", "items": [{"product_id": "sugar-1kg", "qty": 1}]},
            {"sale_id": "S0", "store_id": "001", "status": "paid", "payment_ref": "REF0",
             "created_at": "2026-09-30T20:00:00+00:00", "items": [{"product_id": "sugar-1kg", "qty": 5}]},
        ]
        self.sync = {"pending": 2, "oldest_pending_at": "2026-10-01T08:00:00+00:00", "attempts": 1,
                     "next_attempt_at": "2026-10-01T10:01:00+00:00", "last_error": "retailer offline"}
        self.online = [  # paid online orders not collected yet (GET /sales?channel=online...)
            {"sale_id": "WEB-00000000000000B2", "store_id": "001", "status": "paid", "channel": "online",
             "customer_name": "Jane", "payment_ref": "REF9", "created_at": "2026-10-01T09:30:00+00:00",
             "ready_at": None, "ready_by": None, "exited_at": None,
             "items": [{"product_id": "milk-500ml", "qty": 2, "unit_price_kes": 65},
                       {"product_id": "sugar-1kg", "qty": 1, "unit_price_kes": 180}]},
            {"sale_id": "WEB-00000000000000A1", "store_id": "001", "status": "paid", "channel": "online",
             "customer_name": "Otieno", "payment_ref": "REF8", "created_at": "2026-10-01T09:00:00+00:00",
             "ready_at": "2026-10-01T09:10:00+00:00", "ready_by": "mary", "exited_at": None,
             "items": [{"product_id": "bread-400g", "qty": 1, "unit_price_kes": 70}]},
        ]
        self.delivered = []  # paid online orders that left the store with a rider
        self.discrepancies = [{"discrepancy_id": 7, "store_id": "001", "product_id": "sugar-1kg", "our_qty": 3,
                               "retailer_qty": 5, "category": "unknown", "status": "open",
                               "created_at": "2026-10-01T07:00:00+00:00"}]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if request.method == "POST" and path.startswith("/discrepancies/"):
            body = json.loads(request.content)
            if path != "/discrepancies/7/count":
                return httpx.Response(422, json={"detail": "No open discrepancy with that id"})
            self.counts.append(body)
            return httpx.Response(200, json={"adjusted_by": body["counted_qty"] - 3, "stock": body["counted_qty"]})
        if request.method == "POST" and path == "/counts":
            body = json.loads(request.content)
            p = next((p for p in self.products if p["ean13"] == body["ean13"]), None)
            if not p:
                return httpx.Response(404, json={"detail": f"No product with barcode {body['ean13']}"})
            self.stock_counts.append(body)
            was = self.stock.get(p["product_id"], 0)
            self.stock[p["product_id"]] = body["counted_qty"]
            return httpx.Response(200, json={"product_id": p["product_id"], "name": p["name"], "counted": body["counted_qty"],
                                             "was": was, "adjusted_by": body["counted_qty"] - was,
                                             "counted_by": body["counted_by"], "stock": body["counted_qty"]})
        if path == "/counts/001":
            return httpx.Response(200, json=[{"count_id": b["count_id"], "counted_qty": b["counted_qty"],
                                              "counted_by": b["counted_by"]} for b in reversed(self.stock_counts)])
        if request.method == "PUT" and path.startswith("/products/"):
            body = json.loads(request.content)
            if any(p["ean13"] == body["ean13"] and p["product_id"] != body["product_id"] for p in self.products):
                return httpx.Response(409, json={"detail": f"Barcode {body['ean13']} already belongs to another product"})
            self.products = [p for p in self.products if p["product_id"] != body["product_id"]] + [body]
            return httpx.Response(200, json={"product_id": body["product_id"]})
        if request.method == "POST" and path == "/restocks":
            body = json.loads(request.content)
            self.restocks.append(body)
            return httpx.Response(200, json={"product_id": "sugar-1kg", "created": True, "stock": 3 + body["qty"]})
        if path == "/sales/lookup":
            code = request.url.params["code"].upper()
            hits = [s for s in self.sales if s["sale_id"].endswith(code)]
            if request.url.params["store_id"] != "001" or not hits:
                return httpx.Response(404, json={"detail": f"No order {code} in this store"})
            return httpx.Response(200, json=hits[0])
        if path == "/sales" and request.url.params.get("channel") == "online":
            assert request.url.params["status"] == "paid" and request.url.params["uncollected"] == "true"
            return httpx.Response(200, json=self.online)
        if request.method == "POST" and path.endswith("/ready"):
            sale = next((s for s in self.online if s["sale_id"] == path.split("/")[2]), None)
            if not sale:
                return httpx.Response(404, json={"detail": "No sale"})
            if not sale["ready_at"]:
                sale.update(ready_at="2026-10-01T10:45:00+00:00", ready_by=json.loads(request.content)["ready_by"])
            return httpx.Response(200, json=sale)
        if request.method == "POST" and path.endswith("/exit"):
            sale = next((s for s in self.sales if s["sale_id"] == path.split("/")[2]), None)
            if sale.get("exited_at"):
                return httpx.Response(409, json={"detail": "already left the store"})
            sale.update(exited_at="2026-10-01T10:40:00+00:00", exited_by=json.loads(request.content)["checked_by"])
            return httpx.Response(200, json=sale)
        if request.method == "GET" and path.startswith("/sales/WEB-"):
            sale = next((s for s in self.online + self.delivered if s["sale_id"] == path.split("/")[2]), None)
            return httpx.Response(200, json=sale) if sale else httpx.Response(404, json={"detail": "No sale"})
        routes = {"/products": self.products, "/stock/001": self.stock, "/sales": self.sales,
                  "/sync/001": self.sync, "/discrepancies/001": self.discrepancies}
        if path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404, json={"detail": "not found"})


class FakeDispatch:
    def __init__(self, inventory: FakeInventory):
        self.inventory = inventory
        self.down = False
        self.calls = []
        self.riders = [{"rider_id": "otieno", "phone": "0722000111", "on_shift": True, "active_jobs": 0},
                       {"rider_id": "akinyi", "phone": "", "on_shift": False, "active_jobs": 0}]
        self.deliveries = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/riders":
            return httpx.Response(200, json=self.riders)
        if path == "/deliveries":
            statuses = request.url.params["status"].split(",")
            return httpx.Response(200, json=[d for d in self.deliveries.values() if d["status"] in statuses])
        _, _, sale_id, action = path.split("/")
        body = json.loads(request.content)
        self.calls.append((sale_id, action, body))
        d = self.deliveries.get(sale_id)
        if not d:
            return httpx.Response(404, json={"detail": "No delivery"})
        if action == "assign":
            d.update(status="assigned", rider_id=body["rider_id"])
        elif action == "release":
            d.update(status="unassigned", rider_id=None)
        elif action == "hand-over":
            if d["status"] != "assigned":
                return httpx.Response(409, json={"detail": "Can't hand over this delivery: it has no rider yet."})
            d["status"] = "picked_up"
        elif action == "retry":
            d.update(status="picked_up", fail_reason="", locked=False)
        return httpx.Response(200, json=d)


@pytest.fixture
def fake():
    return FakeInventory()


@pytest.fixture
def files(tmp_path):
    config = tmp_path / "catalogue.json"
    config.write_text(json.dumps(CATALOGUE))
    return {"config": config, "events": tmp_path / "shelf_events.jsonl"}


@pytest.fixture
def settings(files):
    return Settings(store_id="001", store_tz="Africa/Nairobi", inventory_url="http://inventory.test",
                    shelf_events_path=str(files["events"]), shelf_config_path=str(files["config"]),
                    low_stock_threshold=5)


@pytest.fixture
def inventory(fake):
    return InventoryClient("http://inventory.test",
                           http=httpx.Client(base_url="http://inventory.test", transport=httpx.MockTransport(fake.handler)))


@pytest.fixture
def client(settings, inventory):
    return TestClient(create_app(settings, inventory))


@pytest.fixture
def dispatch_fake(fake):
    return FakeDispatch(fake)


@pytest.fixture
def delivery_client(settings, inventory, dispatch_fake):
    dispatch = DispatchClient("http://dispatch.test", http=httpx.Client(
        base_url="http://dispatch.test", transport=httpx.MockTransport(dispatch_fake.handler)))
    return TestClient(create_app(settings, inventory, dispatch))


def write_events(path, events):
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
