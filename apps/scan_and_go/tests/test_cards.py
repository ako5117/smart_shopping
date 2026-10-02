MILK = "milk-500ml"


def test_pay_by_card_at_the_store(client, payments):
    payments.cards = True
    assert client.get("/api/store").json()["card"] is True
    r = client.post("/api/checkout", json={"method": "card", "email": "tourist@example.com",
                                           "items": [{"product_id": MILK, "qty": 2}]})
    assert r.status_code == 201
    o = r.json()
    assert o["checkout_url"] == f"/card-sim/pay/{o['payment_id']}"
    assert payments.card_checkouts == [{"sale_id": o["order_id"], "amount": 130, "email": "tourist@example.com",
                                        "return_url": f"http://testserver/?order={o['order_id']}"}]
    assert payments.pushes == []

    payments.payments[o["payment_id"]].update(card_brand="Visa", card_last4="4081")
    payments.settle(o["payment_id"], "paid")
    s = client.get(f"/api/orders/{o['order_id']}/payments/{o['payment_id']}").json()
    assert (s["payment_status"], s["payment_method"], s["card"]) == ("paid", "card", "Visa •••• 4081")


def test_no_cards_when_switched_off(client, payments, inventory):
    r = client.post("/api/checkout", json={"method": "card", "email": "x@y.co", "items": [{"product_id": MILK, "qty": 1}]})
    assert r.status_code == 503 and "M-Pesa" in r.json()["detail"]
    assert all(s["status"] == "cancelled" for s in inventory.sales.values())


def test_card_needs_an_email(client, payments, inventory):
    payments.cards = True
    r = client.post("/api/checkout", json={"method": "card", "items": [{"product_id": MILK, "qty": 1}]})
    assert r.status_code == 422
    assert all(s["status"] == "cancelled" for s in inventory.sales.values())
    assert client.post("/api/checkout", json={"items": [{"product_id": MILK, "qty": 1}]}).status_code == 422  # no M-Pesa number


def test_switch_to_card_after_mpesa_fails(client, payments):
    payments.cards = True
    o = client.post("/api/checkout", json={"phone": "0712345678", "items": [{"product_id": MILK, "qty": 1}]}).json()
    payments.settle(o["payment_id"], "cancelled")
    r = client.post(f"/api/orders/{o['order_id']}/pay", json={"method": "card", "email": "tourist@example.com"})
    assert r.status_code == 201 and r.json()["checkout_url"]
