from fastapi.testclient import TestClient

from inventory.api import create_app
from inventory.db import Database


def client():
    c = TestClient(create_app(Database(":memory:")))
    c.put("/products/milk-500ml", json={"product_id": "milk-500ml", "ean13": "6161000000040", "name": "Milk 500 ml"})
    return c


def test_checkout_flow_over_http():
    c = client()
    assert c.post("/restocks", json={"store_id": "001", "ean13": "6161000000040", "qty": 12, "scan_id": "s1"}).json()["stock"] == 12
    assert c.post("/sales", json={"sale_id": "SALE-1", "store_id": "001",
                                  "items": [{"product_id": "milk-500ml", "qty": 2}]}).status_code == 201
    r = c.post("/sales/SALE-1/commit", json={"payment_ref": "NLJ7RT61SV"})
    assert r.json()["status"] == "paid"
    assert c.get("/stock/001").json() == {"milk-500ml": 10}
    assert c.get("/stock/001/milk-500ml").json()["history"][0]["entry_type"] == "sale"


def test_errors_map_to_http_codes():
    c = client()
    assert c.post("/sales/NOPE/commit", json={"payment_ref": "x"}).status_code == 404
    assert c.post("/restocks", json={"store_id": "001", "ean13": "123", "qty": 1, "scan_id": "s"}).status_code == 422
    c.post("/sales", json={"sale_id": "S", "store_id": "001", "items": [{"product_id": "milk-500ml", "qty": 1}]})
    assert c.post("/sales", json={"sale_id": "S", "store_id": "001",
                                  "items": [{"product_id": "milk-500ml", "qty": 5}]}).status_code == 409


def test_reconcile_and_count_over_http():
    c = client()
    c.post("/restocks", json={"store_id": "001", "ean13": "6161000000040", "qty": 12, "scan_id": "s1"})
    [d] = c.post("/reconcile", json={"store_id": "001", "stock": {"milk-500ml": 11}}).json()["differences"]
    # 12 unsent units would explain a retailer figure of 0, not 11, so this needs a count
    assert d["category"] == "unknown"
    [open_d] = c.get("/discrepancies/001").json()
    r = c.post(f"/discrepancies/{open_d['discrepancy_id']}/count", json={"counted_qty": 11, "counted_by": "Mary"})
    assert r.json()["stock"] == 11
