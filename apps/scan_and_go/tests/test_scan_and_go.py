from scan_and_go.app import RateLimiter, ean13_valid, mask_phone, phone_key
from tests.conftest import BREAD, MILK, SUGAR


def checkout(client, items, phone="0712345678"):
    return client.post("/api/checkout", json={"phone": phone, "items": items})


def test_barcode_checks():
    assert ean13_valid(MILK) and ean13_valid("4006381333931")
    assert not ean13_valid("6161000000041")  # wrong check digit
    assert not ean13_valid("616100000004") and not ean13_valid("abcdefghijklm")
    assert phone_key("0712 345 678") == phone_key("+254712345678") == "254712345678"
    assert mask_phone("254712345678") == "254712***678"


def test_product_lookup(client):
    assert client.get(f"/api/products/{MILK}").json() == {"product_id": "milk-500ml", "ean13": MILK,
                                                         "name": "Milk 500 ml", "price": 65}
    assert client.get("/api/products/6161000000041").status_code == 422
    assert client.get("/api/products/4006381333931").status_code == 404  # valid barcode, not our product
    r = client.get(f"/api/products/{BREAD}")
    assert r.status_code == 409 and "Bread 400 g" in r.json()["detail"]


def test_checkout_prices_on_the_server(client, inventory, payments):
    r = checkout(client, [{"product_id": "milk-500ml", "qty": 2}, {"product_id": "sugar-1kg", "qty": 1},
                          {"product_id": "milk-500ml", "qty": 1}])
    assert r.status_code == 201
    order = r.json()
    assert order["total"] == 3 * 65 + 180
    assert order["order_id"].startswith("SG-") and len(order["order_id"]) == 19
    sale = inventory.sales[order["order_id"]]
    assert sale["store_id"] == "001"
    assert [(i["product_id"], i["qty"]) for i in sale["items"]] == [("milk-500ml", 3), ("sugar-1kg", 1)]
    assert payments.pushes == [{"sale_id": order["order_id"], "phone": "0712345678", "amount": 375}]


def test_checkout_rejects_what_cannot_be_sold(client, payments):
    assert checkout(client, [{"product_id": "bread-400g", "qty": 1}]).status_code == 409  # no price
    assert checkout(client, [{"product_id": "ghost", "qty": 1}]).status_code == 409
    assert checkout(client, [{"product_id": "milk-500ml", "qty": 21}]).status_code == 422
    assert checkout(client, [{"product_id": "milk-500ml", "qty": 0}]).status_code == 422
    assert checkout(client, []).status_code == 422
    assert payments.pushes == []


def test_payment_flow_to_receipt(client, payments):
    order = checkout(client, [{"product_id": "milk-500ml", "qty": 2}]).json()
    url = f"/api/orders/{order['order_id']}/payments/{order['payment_id']}"
    assert client.get(url).json()["payment_status"] == "pending"
    assert payments.refreshes == 0  # too early to ask Daraja directly

    payments.settle(order["payment_id"], "paid", receipt="SJT4K2L9QX")
    s = client.get(url).json()
    assert s["payment_status"] == "paid" and s["sale_status"] == "paid"
    assert s["mpesa_receipt"] == "SJT4K2L9QX" and s["amount"] == 130 and s["total"] == 130
    assert s["phone"] == "254712***678"
    assert s["lines"] == [{"product_id": "milk-500ml", "name": "Milk 500 ml", "qty": 2, "price": 65, "line_total": 130}]


def test_slow_callback_triggers_refresh(client, payments):
    order = checkout(client, [{"product_id": "milk-500ml", "qty": 1}]).json()
    payments.age(order["payment_id"], 20)
    client.get(f"/api/orders/{order['order_id']}/payments/{order['payment_id']}")
    assert payments.refreshes == 1


def test_order_lookup_needs_matching_ids(client, payments):
    a = checkout(client, [{"product_id": "milk-500ml", "qty": 1}]).json()
    b = checkout(client, [{"product_id": "sugar-1kg", "qty": 1}], phone="0722000000").json()
    assert client.get(f"/api/orders/{a['order_id']}/payments/{b['payment_id']}").status_code == 404
    assert client.get(f"/api/orders/SG-0000000000000000/payments/{a['payment_id']}").status_code == 404
    assert client.get(f"/api/orders/not-an-order/payments/{a['payment_id']}").status_code == 404


def test_retry_after_cancel(client, payments):
    order = checkout(client, [{"product_id": "sugar-1kg", "qty": 2}]).json()
    payments.settle(order["payment_id"], "cancelled", desc="Request cancelled by user")
    url = f"/api/orders/{order['order_id']}/payments/{order['payment_id']}"
    assert client.get(url).json()["payment_status"] == "cancelled"

    r = client.post(f"/api/orders/{order['order_id']}/pay", json={"phone": "0712345678"})
    assert r.status_code == 201 and r.json()["total"] == 360
    assert payments.pushes[-1] == {"sale_id": order["order_id"], "phone": "0712345678", "amount": 360}

    payments.settle(r.json()["payment_id"], "paid", receipt="ABC123")
    assert client.post(f"/api/orders/{order['order_id']}/pay", json={"phone": "0712345678"}).status_code == 409


def test_failed_stk_push_cancels_the_sale(client, inventory, payments):
    payments.fail_push = 422
    r = checkout(client, [{"product_id": "milk-500ml", "qty": 1}], phone="0712345678")
    assert r.status_code == 422 and "Safaricom number" in r.json()["detail"]
    [sale] = inventory.sales.values()
    assert sale["status"] == "cancelled"

    payments.fail_push = 502
    assert checkout(client, [{"product_id": "milk-500ml", "qty": 1}], phone="0733000000").status_code == 502


def test_rate_limit_per_phone(client, payments):
    for _ in range(3):
        assert checkout(client, [{"product_id": "milk-500ml", "qty": 1}], phone="0712345678").status_code == 201
    r = checkout(client, [{"product_id": "milk-500ml", "qty": 1}], phone="+254 712 345 678")
    assert r.status_code == 429
    assert len(payments.pushes) == 3
    assert checkout(client, [{"product_id": "milk-500ml", "qty": 1}], phone="0722000000").status_code == 201

    limiter = RateLimiter(1, 600)
    assert limiter.allow("x", now=0) and not limiter.allow("x", now=599) and limiter.allow("x", now=601)


def test_store_offline(client, inventory):
    inventory.down = True
    r = client.get(f"/api/products/{SUGAR}")
    assert r.status_code == 503 and "till" in r.json()["detail"]


def test_page_and_store_info(client):
    assert "<title>Scan &amp; Go</title>" in client.get("/").text
    assert client.get("/api/store").json() == {"store_id": "001", "name": "Test Store", "currency": "KES"}


def test_pass_shows_exit_check(client, inventory, payments):
    order = checkout(client, [{"product_id": "milk-500ml", "qty": 1}]).json()
    payments.settle(order["payment_id"], "paid", receipt="R1")
    url = f"/api/orders/{order['order_id']}/payments/{order['payment_id']}"
    assert client.get(url).json()["exited_at"] is None
    inventory.sales[order["order_id"]]["exited_at"] = "2026-10-01T10:05:00+00:00"
    assert client.get(url).json()["exited_at"] == "2026-10-01T10:05:00+00:00"


def test_pass_qr_code(client):
    r = client.get("/api/orders/SG-0123456789ABCDEF/qr.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert 'xmlns="http://www.w3.org/2000/svg"' in r.text  # needed for the browser to show it as an <img>
    assert client.get("/api/orders/not-an-order/qr.svg").status_code == 404


def test_prices_come_from_inventory(client, inventory):
    inventory.products[2]["price_kes"] = 70  # staff priced the bread in the dashboard
    assert client.get(f"/api/products/{BREAD}").json()["price"] == 70


def test_charges_the_price_saved_on_the_sale(client, inventory, payments):
    client.get(f"/api/products/{MILK}")  # the app now has milk at 65 in its cache
    inventory.products[0]["price_kes"] = 70  # staff raise the price; the sale is made at 70
    order = checkout(client, [{"product_id": "milk-500ml", "qty": 2}]).json()
    assert order["total"] == 140 and payments.pushes[-1]["amount"] == 140


def test_product_priced_away_between_scan_and_checkout(client, inventory, payments):
    client.get(f"/api/products/{MILK}")
    inventory.products[0]["price_kes"] = None
    r = checkout(client, [{"product_id": "milk-500ml", "qty": 1}])
    assert r.status_code == 409 and payments.pushes == []
    [sale] = inventory.sales.values()
    assert sale["status"] == "cancelled"
