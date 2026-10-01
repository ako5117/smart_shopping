def test_pages_are_served(client):
    for path, title in (("/products", "Products"), ("/exit", "Exit check")):
        r = client.get(path)
        assert r.status_code == 200 and f"<title>{title}" in r.text
    assert "--accent" in client.get("/static/app.css").text


def test_products_list_includes_stock(client):
    products = client.get("/api/products").json()
    assert [(p["product_id"], p["stock"]) for p in products] == [("milk-500ml", 20), ("sugar-1kg", 3)]  # by name


def test_save_product(client, fake):
    r = client.put("/api/products/bread-400g", json={"ean13": "6161000000064", "name": "Bread 400 g", "price_kes": 70})
    assert r.status_code == 200
    assert fake.products[-1] == {"product_id": "bread-400g", "ean13": "6161000000064", "name": "Bread 400 g", "price_kes": 70}
    clash = client.put("/api/products/other", json={"ean13": "6161000000064", "name": "Other"})
    assert clash.status_code == 409 and "already belongs" in clash.json()["detail"]
    assert client.put("/api/products/x", json={"ean13": "123", "name": "X"}).status_code == 422
    assert client.put("/api/products/x", json={"ean13": "6161000000064", "name": "X", "price_kes": 0}).status_code == 422


def test_restock_goes_to_this_store(client, fake):
    r = client.post("/api/restocks", json={"ean13": "6161000000026", "qty": 6, "scan_id": "b6f0c7a2-restock"})
    assert r.status_code == 200 and r.json()["stock"] == 9
    assert fake.restocks == [{"ean13": "6161000000026", "qty": 6, "scan_id": "b6f0c7a2-restock", "store_id": "001"}]
    assert client.post("/api/restocks", json={"ean13": "6161000000026", "qty": 0, "scan_id": "b6f0c7a2-x"}).status_code == 422


def test_exit_check(client, fake):
    missing = client.get("/api/exit/lookup", params={"code": "ZZZZ9999"})
    assert missing.status_code == 404 and "No order" in missing.json()["detail"]
    r = client.get("/api/exit/lookup", params={"code": "s2"})  # codes are not case-sensitive
    sale = r.json()
    assert sale["sale_id"] == "S2" and sale["item_count"] == 3
    assert sale["items"][0]["name"] == "Milk 500 ml"
    done = client.post("/api/exit/S2", json={"checked_by": " Mary "})
    assert done.status_code == 200 and done.json()["exited_by"] == "Mary"
    again = client.post("/api/exit/S2", json={"checked_by": "John"})
    assert again.status_code == 409 and again.json()["detail"] == "already left the store"
    assert client.post("/api/exit/S2", json={"checked_by": ""}).status_code == 422


def test_admin_when_inventory_is_down(client, fake):
    fake.down = True
    assert client.get("/api/products").status_code == 502
    assert client.post("/api/restocks", json={"ean13": "6161000000026", "qty": 1, "scan_id": "abcdefgh"}).status_code == 502


def test_shelf_products_for_matching_codes(client):
    assert client.get("/api/shelf-products").json() == [
        {"product_id": "sugar-1kg", "ean13": "6161000000026", "name": "Sugar 1 kg"},
        {"product_id": "milk-500ml", "ean13": "6161000000040", "name": "Milk 500 ml"},
    ]
