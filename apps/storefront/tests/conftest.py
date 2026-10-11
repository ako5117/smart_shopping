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
        self.returns = []
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
        if path == "/products/milk-500ml/image":
            if request.headers.get("if-none-match") == '"v7"':
                return httpx.Response(304, headers={"ETag": '"v7"'})
            return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"Content-Type": "image/jpeg", "ETag": '"v7"'})
        if path.startswith("/products/") and path.endswith("/image"):
            return httpx.Response(404, json={"detail": "No photo"})
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
                                           "customer_phone": body.get("customer_phone", ""),
                                           "loyalty_discount_kes": body.get("loyalty_discount_kes", 0),
                                           "customer_id": body.get("customer_id", ""),
                                           "status": "pending_payment", "payment_ref": None, "exited_at": None,
                                           "ready_at": None, "created_at": NOW.isoformat()}
            return httpx.Response(201, json=self.sales[body["sale_id"]])
        if method == "POST" and path == "/returns":
            body = json.loads(request.content)
            self.returns.append(body)
            if body["lines"][0]["product_id"] == "milk-500ml":
                return httpx.Response(409, json={"detail": "Milk 500 ml: Food and other perishables can't be returned"})
            return httpx.Response(201, json={"return_id": "RET-1", "status": "requested", **body})
        if path.startswith("/sales/") and path.endswith("/returnable"):
            sale = self.sales.get(path.split("/")[2])
            return httpx.Response(200, json={"sale_id": sale["sale_id"], "open": sale["status"] == "paid", "days": 14,
                                             "items": [], "returns": []}) if sale else httpx.Response(404, json={"detail": "No sale"})
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
        self.cards = True
        self.card_checkouts = []
        self.refreshes = []

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
        if path == "/payments/methods":
            return httpx.Response(200, json={"mpesa": True, "card": self.cards, "card_provider": "simulator"})
        if method == "POST" and path == "/payments/card/checkout":
            body = json.loads(request.content)
            self.card_checkouts.append(body)
            if not self.cards:
                return httpx.Response(404, json={"detail": "off"})
            pid = f"card-{len(self.card_checkouts)}"
            self.payments[pid] = {"payment_id": pid, "sale_id": body["sale_id"], "method": "card", "status": "pending",
                                  "amount": body["amount"], "phone_number": "", "email": body["email"],
                                  "mpesa_receipt_number": None, "result_desc": None, "card_brand": None,
                                  "card_last4": None, "created_at": NOW.isoformat(), "updated_at": NOW.isoformat()}
            return httpx.Response(201, json={"payment_id": pid, "status": "pending",
                                             "checkout_url": f"/card-sim/pay/{pid}"})
        if path.startswith("/payments/"):
            parts = path.split("/")
            if method == "POST" and parts[-1] == "refresh":
                self.refreshes.append(parts[2])
            p = self.payments.get(parts[2])
            if not p:
                return httpx.Response(404, json={"detail": "Payment not found"})
            return httpx.Response(200, json=p)
        return httpx.Response(404)

    def settle(self, pid, status, receipt=None):
        p = self.payments[pid]
        p.update(status=status, mpesa_receipt_number=receipt)
        if p.get("method") == "card" and status == "paid":
            p.update(card_brand="Visa", card_last4="4081")
        if status == "paid":
            self.inventory.pay(p["sale_id"])


DELIVERY_SETTINGS = {"store": {"lat": -1.2864, "lng": 36.8172}, "max_km": 20.0, "base_kes": 100, "per_km_kes": 20,
                     "express_multiplier": 1.5, "road_factor": 1.3, "hours": "08:00–20:00", "express_minutes": 60}
SLOTS = [{"start": "2026-10-01T09:00:00+00:00", "end": "2026-10-01T11:00:00+00:00", "label": "Today 12:00–14:00"},
         {"start": "2026-10-01T11:00:00+00:00", "end": "2026-10-01T13:00:00+00:00", "label": "Today 14:00–16:00"}]


class FakeDispatch:
    def __init__(self):
        self.down = False
        self.express_open = True
        self.refuse = False
        self.deliveries = {}
        self.riders = {"otieno": {"rider_id": "otieno", "phone": "0722000111", "on_shift": True}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path, method = request.url.path, request.method
        if path == "/delivery":
            return httpx.Response(200, json=DELIVERY_SETTINGS)
        if path == "/delivery/quote":
            lat = float(request.url.params["lat"])
            if lat > -1.2:  # the fake's "far away": north of the city
                return httpx.Response(200, json={"deliverable": False, "distance_km": 51.6, "max_km": 20.0,
                                                 "reason": "You're about 51.6 km from the store. We deliver within 20 km, "
                                                           "so this order would need to be collected from the store."})
            return httpx.Response(200, json={
                "deliverable": True, "distance_km": 4.4, "max_km": 20.0, "reason": "",
                "express": {"available": self.express_open, "fee_kes": 290, "eta_minutes": 60},
                "scheduled": {"available": True, "fee_kes": 190, "slots": SLOTS}})
        if method == "POST" and path == "/deliveries":
            if self.refuse:
                return httpx.Response(422, json={"detail": "nope"})
            body = json.loads(request.content)
            fee = 290 if body["tier"] == "express" else 190
            slot = next((x["label"] for x in SLOTS if x["start"] == body.get("slot_start")), "Express")
            self.deliveries[body["sale_id"]] = {**body, "status": "awaiting_payment", "fee_kes": fee, "pin": "4821",
                                                "slot_label": slot, "distance_km": 4.4,
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


LOYALTY = {"loyalty_enabled": True, "kes_per_point": 100, "redeem_points": 100, "redeem_kes": 50, "min_age": 18,
           "privacy_version": "2026-10", "email_provider": "outbox"}


class FakeCustomers:
    """The Customers Service as the shop sees it: one customer, Jane, whose code is 123456."""

    def __init__(self):
        self.down = False
        self.points = 250
        self.orders = {}
        self.cancelled = []
        self.signups = []
        self.photo = None

    def profile(self):
        return {"customer_id": "C1", "name": "Jane Wanjiru", "phone": "254712345678", "email": "jane@example.com",
                "dob": "1994-05-17", "gender": "female", "photo_version": "p1" if self.photo else None,
                "loyalty": {"enabled": True, "points": self.points, "worth_kes": self.points // 100 * 50}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path, method = request.url.path, request.method
        signed_in = request.headers.get("x-session") == "tok-jane"
        body = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else {}
        if path == "/settings":
            return httpx.Response(200, json=LOYALTY)
        if path == "/signup":
            self.signups.append(body)
            return httpx.Response(202, json={"email": body["email"].lower(), "sent": True}) if body.get("consent") else \
                httpx.Response(422, json={"detail": "Please read and accept the privacy notice to join."})
        if path == "/codes":
            return httpx.Response(202, json={"email": body["email"], "sent": True})
        if path == "/sessions" and method == "POST":
            if body["code"] != "123456":
                return httpx.Response(422, json={"detail": {"message": "That code isn't right. 4 tries left.", "attempts_left": 4}})
            return httpx.Response(201, json={"token": "tok-jane", "expires_at": "x", "customer": self.profile()})
        if path == "/sessions" and method == "DELETE":
            return httpx.Response(204)
        if path.startswith("/orders/") and path.endswith("/cancel"):
            sale_id = path.split("/")[2]
            self.cancelled.append(sale_id)
            o = self.orders.get(sale_id)
            if o and o["status"] == "pending":
                o["status"] = "cancelled"
                self.points += o["redeemed_points"]
            return httpx.Response(200, json=o or {})
        if not signed_in:
            return httpx.Response(404, json={"detail": "Not signed in"})
        if path == "/me" and method == "GET":
            return httpx.Response(200, json=self.profile())
        if path == "/me" and method == "PUT":
            return httpx.Response(200, json={**self.profile(), "name": body["name"]})
        if path == "/me" and method == "DELETE":
            return httpx.Response(204)
        if path == "/me/photo":
            if method == "PUT":
                self.photo = request.content
                return httpx.Response(200, json=self.profile())
            if method == "GET":
                return httpx.Response(200, content=b"\xff\xd8jpeg") if self.photo else httpx.Response(404, json={"detail": "No photo"})
        if path == "/me/orders":
            return httpx.Response(200, json=[o for o in self.orders.values() if o["status"] != "cancelled"])
        if path == "/orders" and method == "POST":
            blocks = min(self.points // 100, (body["goods_kes"] - 1) // 50) if body["use_points"] else 0
            self.points -= blocks * 100
            self.orders[body["sale_id"]] = {"sale_id": body["sale_id"], "customer_id": "C1", "status": "pending",
                                            "redeemed_points": blocks * 100, "discount_kes": blocks * 50,
                                            "earned_points": 0, "created_at": NOW.isoformat()}
            return httpx.Response(201, json=self.orders[body["sale_id"]])
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def customers():
    return FakeCustomers()


@pytest.fixture
def member(inventory, payments, dispatch, customers, shelf_log, clock):
    """A shop with accounts (the Customers Service), signed in as Jane."""
    settings = Settings(store_id="001", store_name="Test Store", inventory_url="http://inv", payments_url="http://pay",
                        shelf_events_path=str(shelf_log), products_cache_s=0, stock_cache_s=0)
    c = TestClient(create_app(
        settings,
        inventory=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(inventory.handler)),
        payments=httpx.Client(base_url="http://pay", transport=httpx.MockTransport(payments.handler)),
        clock=lambda: clock["now"],
        dispatch=httpx.Client(base_url="http://dispatch", transport=httpx.MockTransport(dispatch.handler)),
        customers=httpx.Client(base_url="http://cust", transport=httpx.MockTransport(customers.handler)),
    ))
    assert c.post("/api/account/verify", json={"email": "jane@example.com", "code": "123456"}).status_code == 200
    return c


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
