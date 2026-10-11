"""Discounts on the dashboard: managers only."""
MARY = {"X-Staff-User": "mary", "X-Staff-Role": "staff"}
ADRIAN = {"X-Staff-User": "adrian", "X-Staff-Role": "manager"}


def test_managers_start_and_end_offers(client, fake):
    offer = {"product_id": "sugar-1kg", "kind": "percent", "value": 10}
    r = client.post("/api/discounts", json=offer, headers=ADRIAN)
    assert r.status_code == 201 and fake.offers[0]["created_by"] == "adrian" and r.json()["sale_price_kes"] == 162
    assert [d["product_id"] for d in client.get("/api/discounts", headers=ADRIAN).json()] == ["sugar-1kg"]
    assert client.post("/api/discounts", json=offer, headers=ADRIAN).status_code == 409
    assert client.post("/api/discounts/1/end", headers=ADRIAN).json()["ended_by"] == "adrian"
    assert client.get("/api/discounts").json() == []


def test_staff_cannot_set_offers(client, fake):
    offer = {"product_id": "sugar-1kg", "kind": "amount", "value": 20}
    assert client.post("/api/discounts", json=offer, headers=MARY).status_code == 403
    assert fake.offers == []
    assert client.post("/api/discounts", json={**offer, "kind": "half"}, headers=ADRIAN).status_code == 422


def test_inventory_down(client, fake):
    fake.down = True
    assert client.get("/api/discounts").status_code == 502
    assert client.post("/api/discounts", json={"product_id": "sugar-1kg", "kind": "percent", "value": 5}, headers=ADRIAN).status_code == 502
