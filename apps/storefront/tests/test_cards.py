from datetime import timedelta

from tests.conftest import NOW


def card_order(client, email="jane@example.co.ke", **kw):
    return client.post("/api/orders", json={"name": "Jane", "phone": "0712345678", "payment_method": "card",
                                            "email": email, "items": [{"product_id": "milk-500ml", "qty": 2}], **kw})


def test_card_order_goes_to_the_hosted_checkout(client, inventory, payments):
    r = card_order(client)
    assert r.status_code == 201
    o = r.json()
    assert o["checkout_url"] == f"/card-sim/pay/{o['payment_id']}" and o["total"] == 130
    [c] = payments.card_checkouts
    # The customer comes back to the shop, which picks the order up again.
    assert c == {"sale_id": o["order_id"], "amount": 130, "email": "jane@example.co.ke",
                 "return_url": f"http://testserver/?order={o['order_id']}"}
    assert payments.pushes == []  # no M-Pesa prompt
    assert inventory.sales[o["order_id"]]["status"] == "pending_payment"


def test_card_needs_an_email_and_cards_switched_on(client, inventory, payments):
    r = card_order(client, email=None)
    assert r.status_code == 422 and "email" in r.json()["detail"]
    [sale] = inventory.sales.values()
    assert sale["status"] == "cancelled"  # nothing left held
    payments.cards = False
    assert client.get("/api/store").json()["card"] is False
    assert card_order(client).status_code == 503


def test_coming_back_from_the_card_page(client, inventory, payments):
    o = card_order(client).json()
    url = f"/api/orders/{o['order_id']}/payments/{o['payment_id']}"
    s = client.get(url).json()
    assert (s["stage"], s["payment_method"]) == ("awaiting_payment", "card")
    assert payments.refreshes == [o["payment_id"]]  # asked the provider straight away, not after 15 s
    payments.settle(o["payment_id"], "paid")
    s = client.get(url).json()
    assert (s["stage"], s["card"], s["mpesa_receipt"]) == ("packing", "Visa •••• 4081", None)


def test_switch_from_card_to_mpesa_and_back(client, inventory, payments, clock):
    o = card_order(client).json()
    payments.settle(o["payment_id"], "failed")
    r = client.post(f"/api/orders/{o['order_id']}/pay", json={"method": "mpesa", "phone": "0712345678"})
    assert r.status_code == 201 and r.json()["checkout_url"] is None and payments.pushes[-1]["amount"] == 130
    payments.settle(r.json()["payment_id"], "cancelled")
    r = client.post(f"/api/orders/{o['order_id']}/pay", json={"method": "card", "email": "jane@example.co.ke"})
    assert r.json()["checkout_url"].startswith("/card-sim/pay/")
    assert client.post(f"/api/orders/{o['order_id']}/pay", json={"method": "mpesa"}).status_code == 422  # no number
    clock["now"] = NOW + timedelta(minutes=16)
    assert client.post(f"/api/orders/{o['order_id']}/pay", json={"method": "card", "email": "jane@example.co.ke"}).status_code == 409
