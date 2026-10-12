"""The shop in Kiswahili: the page sends X-Language: sw, and what the server says comes back in it."""
SW = {"X-Language": "sw"}


def order(client, headers=None, **kw):
    return client.post("/api/orders", headers=headers or {}, json={
        "name": "Jane", "phone": "0712345678", "items": [{"product_id": "milk-500ml", "qty": 1}], **kw})


def test_errors_in_the_page_language(client):
    r = client.get("/api/orders/WEB-123/payments/nope", headers=SW)
    assert (r.status_code, r.json()["detail"]) == (404, "Oda haikupatikana")
    assert client.get("/api/orders/WEB-123/payments/nope").json()["detail"] == "Order not found"
    r = client.post("/api/orders", headers=SW, json={"name": "Jane", "phone": "0712345678",
                                                    "items": [{"product_id": "milk-500ml", "qty": 99}]})
    assert r.json()["detail"] == "Tafadhali, hadi 20 za bidhaa moja kwa kila oda."


def test_delivery_quote_in_kiswahili(client):
    q = client.get("/api/delivery/quote", params={"lat": -1.3, "lng": 36.8}, headers=SW).json()
    assert [s["label"] for s in q["scheduled"]["slots"]] == ["Leo 12:00–14:00", "Leo 14:00–16:00"]
    far = client.get("/api/delivery/quote", params={"lat": -1.0, "lng": 36.8}, headers=SW).json()
    assert far["reason"].startswith("Uko takriban km 51.6 kutoka dukani.")
    assert client.get("/api/delivery/quote", params={"lat": -1.3, "lng": 36.8}).json()["scheduled"]["slots"][0]["label"] == \
        "Today 12:00–14:00"


def test_the_order_records_the_language_for_its_texts(client, inventory):
    a, b = order(client, SW).json(), order(client).json()
    assert inventory.sales[a["order_id"]]["language"] == "sw"
    assert inventory.sales[b["order_id"]]["language"] == "en"


def test_offers_in_kiswahili(client, inventory):
    inventory.products[0].update(sale_price_kes=55, discount={"kind": "percent", "value": 15})
    milk = next(p for p in client.get("/api/catalogue", headers=SW).json()["products"] if p["product_id"] == "milk-500ml")
    assert milk["offer"] == "Punguzo la 15%"


def test_account_language(member, customers):
    assert member.get("/api/account").json()["customer"]["language"] == "en"
    assert member.put("/api/account/language", json={"language": "sw"}).json()["language"] == "sw"
    assert member.put("/api/account/language", json={"language": "fr"}).status_code == 422
    member.post("/api/account/code", json={"email": "jane@example.com"}, headers=SW)
    assert customers.codes[-1]["language"] == "sw"  # the sign-in email comes in the page's language
    member.post("/api/account/signup", headers=SW, json={"name": "Amos", "phone": "0722000111", "email": "amos@example.com",
                                                         "dob": "1990-01-01", "consent": True})
    assert customers.signups[-1]["language"] == "sw"
