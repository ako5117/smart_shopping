from storefront.account import COOKIE


def order(client, use_points=False, qty=4, phone="0712345678", **kw):
    return client.post("/api/orders", json={"name": "Jane", "phone": phone, "use_points": use_points,
                                            "items": [{"product_id": "milk-500ml", "qty": qty}], **kw})


def test_store_says_accounts_and_loyalty_are_on(member):
    a = member.get("/api/store").json()["accounts"]
    assert (a["available"], a["loyalty_enabled"], a["kes_per_point"], a["redeem_points"], a["redeem_kes"]) == (True, True, 100, 100, 50)


def test_sign_in_sets_a_private_cookie(member, customers):
    me = member.get("/api/account").json()
    assert me["signed_in"] and me["customer"]["name"] == "Jane Wanjiru"
    assert member.cookies.get(COOKIE) == "tok-jane"
    member.post("/api/account/signout")
    assert member.get("/api/account").json()["signed_in"] is False


def test_cookie_flags(inventory, payments, customers):
    from fastapi.testclient import TestClient
    import httpx
    from storefront.app import create_app
    from storefront.config import Settings
    c = TestClient(create_app(Settings(store_id="001", store_name="T", inventory_url="http://inv", payments_url="http://pay"),
                              inventory=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(inventory.handler)),
                              payments=httpx.Client(base_url="http://pay", transport=httpx.MockTransport(payments.handler)),
                              customers=httpx.Client(base_url="http://c", transport=httpx.MockTransport(customers.handler))),
                   base_url="https://shop.example")
    r = c.post("/api/account/verify", json={"email": "jane@example.com", "code": "123456"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie and "path=/" in cookie
    bad = c.post("/api/account/verify", json={"email": "jane@example.com", "code": "000000"})
    assert bad.status_code == 422 and bad.json()["detail"] == "That code isn't right. 4 tries left."


def test_signup_passes_through_and_is_rate_limited(client, member, customers):
    jane = {"name": "Jane", "phone": "0712345678", "email": "jane@example.com", "dob": "1994-05-17", "consent": True}
    assert member.post("/api/account/signup", json=jane).status_code == 202
    assert member.post("/api/account/signup", json={**jane, "consent": False}).status_code == 422
    codes = [member.post("/api/account/code", json={"email": "jane@example.com"}).status_code for _ in range(10)]
    assert 429 in codes


def test_signed_in_orders_spend_and_earn_points(member, inventory, payments, customers):
    r = order(member, use_points=True)  # 4 x 65 = 260; 250 points: 2 blocks = KES 100 off
    assert r.status_code == 201
    o = r.json()
    assert (o["total"], o["points_discount"], payments.pushes[-1]["amount"]) == (160, 100, 160)
    sale = inventory.sales[o["order_id"]]
    assert (sale["loyalty_discount_kes"], sale["customer_id"]) == (100, "C1")
    payments.settle(o["payment_id"], "paid", receipt="SJT4K2L9QX")
    s = member.get(f"/api/orders/{o['order_id']}/payments/{o['payment_id']}").json()
    assert (s["points_discount"], s["total"], s["points"]) == (100, 160, {"earn": 1, "discount": 100, "spent": 200})


def test_points_come_back_when_payment_cannot_start(member, inventory, payments, customers):
    payments.fail_push = 422  # e.g. not a Safaricom number
    r = order(member, use_points=True)
    assert r.status_code == 422
    [sale] = inventory.sales.values()
    assert sale["status"] == "cancelled" and customers.cancelled == [sale["sale_id"]] and customers.points == 250


def test_points_need_a_signed_in_customer(client):
    assert order(client, use_points=True).status_code == 401
    r = order(client)
    assert r.status_code == 201 and r.json()["points_discount"] == 0


def test_customers_service_down(member, customers, inventory):
    customers.down = True
    assert order(member, use_points=True).status_code == 503  # can't spend points: say so, take nothing
    assert inventory.sales == {}
    r = order(member)  # without points the order goes ahead (it just won't earn)
    assert r.status_code == 201 and inventory.sales[r.json()["order_id"]]["customer_id"] == ""
    assert member.get("/api/account").json()["signed_in"] is False


def test_order_history_follows_the_account(member, payments):
    o = order(member).json()
    payments.settle(o["payment_id"], "paid", receipt="R1")
    [h] = member.get("/api/account/orders").json()
    assert (h["order_id"], h["status"], h["total"]) == (o["order_id"], "paid", 260)
    assert [(l["product_id"], l["qty"]) for l in h["lines"]] == [("milk-500ml", 4)]


def test_profile_photo_and_delete(member, customers):
    assert member.put("/api/account", json={"name": "Jane W", "phone": "0712345678", "dob": "1994-05-17"}).json()["name"] == "Jane W"
    assert member.get("/api/account/photo").status_code == 404
    member.put("/api/account/photo", content=b"\xff\xd8photo", headers={"Content-Type": "image/jpeg"})
    assert member.get("/api/account/photo").content == b"\xff\xd8jpeg"
    assert member.delete("/api/account").json() == {"deleted": True}
    assert not member.cookies.get(COOKIE)


def test_shop_without_accounts(client):
    assert client.get("/api/account").json() == {"available": False, "signed_in": False}
    assert client.post("/api/account/code", json={"email": "a@b.co"}).status_code == 404


def test_problem_reports(member, customers):
    r = member.post("/api/reports", json={"kind": "app", "message": "The pay button did nothing",
                                          "context": {"page": "checkout", "errors": "TypeError"}})
    assert r.status_code == 201 and r.json()["status"] == "open"
    sent = customers.reports[-1]
    assert (sent["app"], sent["customer_id"], sent["context"]["page"]) == ("store", "C1", "checkout")
    assert "user_agent" in sent["context"]  # the browser, for app problems
    assert [x["report_id"] for x in member.get("/api/account/reports").json()] == [r.json()["report_id"]]
    assert member.get(f"/api/reports/{r.json()['report_id']}").json()["status"] == "open"
    assert member.get("/api/reports/R-123").status_code == 404
    assert member.post("/api/reports", json={"kind": "rant", "message": "hello there"}).status_code == 422
    member.post("/api/reports", json={"kind": "idea", "message": "More fruit please", "context": {"page": "x"}})
    assert customers.reports[-1]["context"] == {}  # only app problems carry page and browser details
    codes = [member.post("/api/reports", json={"kind": "idea", "message": "More fruit please"}).status_code for _ in range(8)]
    assert 429 in codes  # a few an hour from one place


def test_reports_without_an_account(client):
    assert client.post("/api/reports", json={"kind": "idea", "message": "More fruit please"}).status_code == 404
