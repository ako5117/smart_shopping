import json

import httpx
import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.config import Settings
from dashboard.sources import InventoryClient

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
        routes = {"/products": self.products, "/stock/001": self.stock, "/sales": self.sales,
                  "/sync/001": self.sync, "/discrepancies/001": self.discrepancies}
        if path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404, json={"detail": "not found"})


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


def write_events(path, events):
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
