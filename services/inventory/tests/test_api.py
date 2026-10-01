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


def test_read_endpoints_for_dashboard():
    c = client()
    assert c.get("/products").json() == [{"product_id": "milk-500ml", "ean13": "6161000000040", "name": "Milk 500 ml",
                                          "price_kes": None}]
    c.post("/restocks", json={"store_id": "001", "ean13": "6161000000040", "qty": 5, "scan_id": "s1"})
    for sale_id in ("S1", "S2"):
        c.post("/sales", json={"sale_id": sale_id, "store_id": "001", "items": [{"product_id": "milk-500ml", "qty": 1}]})
    c.post("/sales/S1/commit", json={"payment_ref": "REF1"})
    sales = c.get("/sales", params={"store_id": "001"}).json()
    assert {s["sale_id"]: s["status"] for s in sales} == {"S1": "paid", "S2": "pending_payment"}
    assert sales[0]["items"] == [{"product_id": "milk-500ml", "qty": 1, "unit_price_kes": None}]
    assert c.get("/sales", params={"store_id": "002"}).json() == []
    status = c.get("/sync/001").json()
    assert status["pending"] == 2 and status["oldest_pending_at"] and status["last_error"] == ""
    assert c.get("/sync/002").json()["pending"] == 0


def test_prices_on_products():
    c = client()
    milk = {"product_id": "milk-500ml", "ean13": "6161000000040", "name": "Milk 500 ml"}
    assert c.get("/products").json()[0]["price_kes"] is None
    assert c.put("/products/milk-500ml", json={**milk, "price_kes": 65}).status_code == 200
    assert c.put("/products/milk-500ml", json={**milk, "name": "Fresh milk 500 ml"}).status_code == 200  # price kept
    assert c.get("/products").json() == [{"product_id": "milk-500ml", "ean13": "6161000000040",
                                          "name": "Fresh milk 500 ml", "price_kes": 65}]
    assert c.put("/products/milk-500ml", json={**milk, "price_kes": 0}).status_code == 422
    clash = {"product_id": "milk-1l", "ean13": "6161000000040", "name": "Milk 1 L"}
    assert c.put("/products/milk-1l", json=clash).status_code == 409  # barcode already used
    assert c.put("/products/Milk 1L", json={**clash, "product_id": "Milk 1L"}).status_code == 422


def test_exit_check():
    c = client()
    c.post("/restocks", json={"store_id": "001", "ean13": "6161000000040", "qty": 5, "scan_id": "s1"})
    for sale_id in ("SG-00000000AAAA1111", "SG-00000000BBBB2222"):
        c.post("/sales", json={"sale_id": sale_id, "store_id": "001", "items": [{"product_id": "milk-500ml", "qty": 1}]})
    c.post("/sales/SG-00000000AAAA1111/commit", json={"payment_ref": "REF1"})

    found = c.get("/sales/lookup", params={"store_id": "001", "code": "aaaa1111"})
    assert found.status_code == 200 and found.json()["sale_id"] == "SG-00000000AAAA1111"
    assert c.get("/sales/lookup", params={"store_id": "002", "code": "AAAA1111"}).status_code == 404
    assert c.get("/sales/lookup", params={"store_id": "001", "code": "1111"}).status_code == 422
    assert c.get("/sales/lookup", params={"store_id": "001", "code": "%%%%%%"}).status_code == 422
    assert c.get("/sales/lookup", params={"store_id": "001", "code": "00000000"}).status_code == 404

    assert c.post("/sales/SG-00000000BBBB2222/exit", json={"checked_by": "Mary"}).status_code == 409  # not paid
    r = c.post("/sales/SG-00000000AAAA1111/exit", json={"checked_by": "Mary"})
    assert r.status_code == 200 and r.json()["exited_by"] == "Mary" and r.json()["exited_at"]
    again = c.post("/sales/SG-00000000AAAA1111/exit", json={"checked_by": "John"})
    assert again.status_code == 409 and "already left" in again.json()["detail"]


def test_older_database_files_get_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("CREATE TABLE products (product_id TEXT PRIMARY KEY, ean13 TEXT UNIQUE NOT NULL, name TEXT NOT NULL);"
                      "INSERT INTO products VALUES ('milk-500ml', '6161000000040', 'Milk 500 ml');")
    old.close()
    db = Database(str(path))
    assert db.one("SELECT * FROM products") == {"product_id": "milk-500ml", "ean13": "6161000000040",
                                                "name": "Milk 500 ml", "price_kes": None}
