from fastapi.testclient import TestClient

from dispatch.api import create_app
from tests.conftest import AREAS, STORE, FakeInventory, fresh_db


def client():
    inventory = FakeInventory()
    return TestClient(create_app(fresh_db(), inventory, AREAS)), inventory


def test_delivery_over_http():
    c, inventory = client()
    assert c.get("/areas").json() == [{"area": "Kilimani", "fee_kes": 150}, {"area": "Westlands", "fee_kes": 200}]
    inventory.sales["WEB-1"] = {"status": "paid"}
    body = {"sale_id": "WEB-1", "store_id": STORE, "customer_name": "Jane", "customer_phone": "0712345678",
            "area": "Westlands", "address": "Apt 3B, Rhapta Rd"}
    r = c.post("/deliveries", json=body)
    assert r.status_code == 201 and r.json()["fee_kes"] == 200
    pin = r.json()["pin"]
    assert c.post("/deliveries", json={**body, "area": "Nakuru"}).status_code == 422
    assert "pin" not in c.get("/deliveries/WEB-1").json()
    assert c.get("/deliveries/WEB-1", params={"include_pin": True}).json()["pin"] == pin
    assert c.get("/deliveries/NOPE").status_code == 404

    assert c.put("/riders/otieno", json={"phone": "0722000111", "on_shift": True}).json()["on_shift"] is True
    assert c.post("/deliveries/WEB-1/claim", json={"rider_id": "otieno"}).json()["status"] == "assigned"
    assert c.post("/deliveries/WEB-1/claim", json={"rider_id": "otieno"}).status_code == 409
    assert c.post("/deliveries/WEB-1/hand-over", json={"by": "mary"}).json()["status"] == "picked_up"
    assert [d["sale_id"] for d in c.get("/deliveries", params={"store_id": STORE, "rider_id": "otieno"}).json()] == ["WEB-1"]

    wrong = "0000" if pin != "0000" else "1111"
    r = c.post("/deliveries/WEB-1/delivered", json={"rider_id": "otieno", "pin": wrong})
    assert r.status_code == 422 and r.json()["detail"]["attempts_left"] == 4
    for _ in range(4):
        r = c.post("/deliveries/WEB-1/delivered", json={"rider_id": "otieno", "pin": wrong})
    assert r.status_code == 423
    c.post("/deliveries/WEB-1/retry", json={"by": "mary"})
    assert c.post("/deliveries/WEB-1/delivered", json={"rider_id": "otieno", "pin": pin}).json()["status"] == "delivered"
    assert c.get("/deliveries", params={"store_id": STORE, "status": "delivered"}).json()[0]["sale_id"] == "WEB-1"
    assert c.get("/deliveries", params={"store_id": STORE, "status": "lost"}).status_code == 422
    assert [e["what"] for e in c.get("/deliveries/WEB-1/events").json()][-1] == "delivered"
