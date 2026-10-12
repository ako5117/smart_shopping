"""Loyalty points in Scan & Go: sign in with an emailed code, earn on what's paid for, spend at checkout."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from scan_and_go.account import COOKIE
from scan_and_go.app import create_app
from scan_and_go.config import Settings

LOYALTY = {"loyalty_enabled": True, "kes_per_point": 100, "redeem_points": 100, "redeem_kes": 50, "email_provider": "outbox"}


class FakeCustomers:
    """The Customers Service as Scan & Go sees it: one customer, Jane, whose code is 123456."""

    def __init__(self):
        self.points, self.orders, self.cancelled, self.codes = 250, {}, [], []
        self.down = False
        self.settings = dict(LOYALTY)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        signed_in = request.headers.get("x-session") == "tok-jane"
        me = {"customer_id": "C1", "name": "Jane Wanjiru", "email": "jane@example.com", "phone": "254712345678",
              "language": "sw", "loyalty": {"points": self.points, "worth_kes": self.points // 100 * 50}}
        if path == "/settings":
            return httpx.Response(200, json=self.settings)
        if path == "/codes":
            self.codes.append(body)
            return httpx.Response(202, json={"email": body["email"], "sent": True})
        if path == "/sessions" and method == "POST":
            if body["code"] != "123456":
                return httpx.Response(422, json={"detail": {"message": "That code isn't right. 4 tries left.", "attempts_left": 4}})
            return httpx.Response(201, json={"token": "tok-jane", "customer": me})
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
        if path == "/me":
            return httpx.Response(200, json=me)
        if path == "/orders" and method == "POST":
            blocks = min(self.points // 100, (body["goods_kes"] - 1) // 50) if body["use_points"] else 0
            self.points -= blocks * 100
            self.orders[body["sale_id"]] = {"sale_id": body["sale_id"], "customer_id": "C1", "status": "pending",
                                            "redeemed_points": blocks * 100, "discount_kes": blocks * 50}
            return httpx.Response(201, json=self.orders[body["sale_id"]])
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def customers():
    return FakeCustomers()


def app_with(inventory, payments, customers):
    settings = Settings(store_id="001", store_name="Test Store", inventory_url="http://inv", payments_url="http://pay")
    return TestClient(create_app(
        settings,
        inventory=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(inventory.handler)),
        payments=httpx.Client(base_url="http://pay", transport=httpx.MockTransport(payments.handler)),
        customers=httpx.Client(base_url="http://c", transport=httpx.MockTransport(customers.handler))))


@pytest.fixture
def shopper(inventory, payments, customers):
    return app_with(inventory, payments, customers)


@pytest.fixture
def member(shopper):
    assert shopper.post("/api/account/verify", json={"email": "jane@example.com", "code": "123456"}).status_code == 200
    return shopper


def checkout(client, use_points=False, qty=4):
    return client.post("/api/checkout", json={"phone": "0712345678", "use_points": use_points,
                                             "items": [{"product_id": "sugar-1kg", "qty": qty}]})


def test_sign_in_with_a_code(shopper, customers):
    assert shopper.get("/api/account").json() == {"available": True, "signed_in": False, "settings": LOYALTY, "customer": None}
    assert shopper.post("/api/account/code", json={"email": "jane@example.com"}, headers={"X-Language": "sw"}).status_code == 202
    assert customers.codes[-1]["language"] == "sw"  # the email comes in the page's language
    bad = shopper.post("/api/account/verify", json={"email": "jane@example.com", "code": "000000"})
    assert (bad.status_code, bad.json()["detail"]) == (422, "That code isn't right. 4 tries left.")
    r = shopper.post("/api/account/verify", json={"email": "jane@example.com", "code": "123456"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and f"{COOKIE}=" in cookie
    me = shopper.get("/api/account").json()
    assert me["signed_in"] and me["customer"] == {"name": "Jane Wanjiru", "points": 250, "worth_kes": 100, "language": "sw"}
    assert "email" not in me["customer"] and "phone" not in me["customer"]  # nothing more than Scan & Go needs
    shopper.post("/api/account/signout")
    assert shopper.get("/api/account").json()["signed_in"] is False


def test_signed_in_purchases_spend_and_earn_points(member, inventory, payments, customers):
    r = checkout(member, use_points=True)  # 4 x 180 = 720; 250 points: 2 blocks = KES 100 off
    assert r.status_code == 201, r.text
    o = r.json()
    assert (o["total"], o["points_discount"], payments.pushes[-1]["amount"]) == (620, 100, 620)
    sale = inventory.sales[o["order_id"]]
    assert (sale["loyalty_discount_kes"], sale["customer_id"]) == (100, "C1")
    payments.settle(o["payment_id"], "paid", receipt="SJT4K2L9QX")
    s = member.get(f"/api/orders/{o['order_id']}/payments/{o['payment_id']}").json()
    assert (s["points_discount"], s["total"], s["points"]) == (100, 620, {"earn": 6, "discount": 100, "spent": 200})


def test_earning_without_spending(member, inventory, customers):
    o = checkout(member).json()
    assert o["points_discount"] == 0 and inventory.sales[o["order_id"]]["customer_id"] == "C1"
    assert customers.orders[o["order_id"]]["redeemed_points"] == 0 and customers.points == 250


def test_points_come_back_when_payment_cannot_start(member, payments, customers, inventory):
    payments.fail_push = 422
    assert checkout(member, use_points=True).status_code == 422
    [sale] = inventory.sales.values()
    assert sale["status"] == "cancelled" and customers.cancelled == [sale["sale_id"]] and customers.points == 250


def test_paying_again_charges_the_discounted_total(member, payments):
    o = checkout(member, use_points=True).json()
    payments.settle(o["payment_id"], "cancelled")
    again = member.post(f"/api/orders/{o['order_id']}/pay", json={"method": "mpesa", "phone": "0712345678"})
    assert (again.json()["total"], payments.pushes[-1]["amount"]) == (620, 620)


def test_signed_out_and_service_down(shopper, member, customers, inventory, client):
    member.post("/api/account/signout")
    assert checkout(shopper, use_points=True).status_code == 401
    r = checkout(shopper)
    assert r.status_code == 201 and inventory.sales[r.json()["order_id"]]["customer_id"] == ""
    customers.down = True
    member.post("/api/account/verify", json={"email": "jane@example.com", "code": "123456"})  # 503: still signed out
    assert checkout(shopper).status_code == 201  # buying never waits on points
    assert client.get("/api/account").json() == {"available": False, "signed_in": False}  # no Customers Service at all


def test_points_switched_off(inventory, payments, customers):
    customers.settings["loyalty_enabled"] = False
    c = app_with(inventory, payments, customers)
    assert c.get("/api/account").json() == {"available": False, "signed_in": False}
    assert checkout(c).status_code == 201
