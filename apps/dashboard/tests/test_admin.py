def test_pages_are_served(client):
    for path, title in (("/products", "Products"), ("/exit", "Exit check")):
        r = client.get(path)
        assert r.status_code == 200 and f"<title>{title}" in r.text
    assert "--accent" in client.get("/static/app.css").text


def test_products_list_includes_stock(client):
    products = client.get("/api/products").json()
    assert [(p["product_id"], p["stock"]) for p in products] == [("milk-500ml", 20), ("sugar-1kg", 3)]  # by name


def test_save_product(client, fake):
    r = client.put("/api/products/bread-400g", json={"ean13": "6161000000064", "name": "Bread 400 g", "price_kes": 70,
                                                     "category": "Bakery"})
    assert r.status_code == 200
    assert fake.products[-1] == {"product_id": "bread-400g", "ean13": "6161000000064", "name": "Bread 400 g",
                                 "price_kes": 70, "category": "Bakery", "changed_by": ""}
    client.put("/api/products/bread-400g", json={"ean13": "6161000000064", "name": "Bread 400 g"})
    assert fake.products[-1] == {"product_id": "bread-400g", "ean13": "6161000000064", "name": "Bread 400 g",
                                 "changed_by": ""}  # no price or category sent: the Inventory Service keeps them
    clash = client.put("/api/products/other", json={"ean13": "6161000000064", "name": "Other"})
    assert clash.status_code == 409 and "already belongs" in clash.json()["detail"]
    assert client.put("/api/products/x", json={"ean13": "123", "name": "X"}).status_code == 422
    assert client.put("/api/products/x", json={"ean13": "6161000000064", "name": "X", "price_kes": 0}).status_code == 422


def test_restock_goes_to_this_store(client, fake):
    r = client.post("/api/restocks", json={"ean13": "6161000000026", "qty": 6, "scan_id": "b6f0c7a2-restock"})
    assert r.status_code == 200 and r.json()["stock"] == 9
    assert fake.restocks == [{"ean13": "6161000000026", "qty": 6, "scan_id": "b6f0c7a2-restock", "store_id": "001",
                              "recorded_by": ""}]
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


MARY = {"X-Staff-User": "mary", "X-Staff-Role": "staff"}
ADRIAN = {"X-Staff-User": "adrian", "X-Staff-Role": "manager"}


def test_who_am_i(client):
    assert client.get("/api/me", headers=MARY).json() == {"name": "mary", "role": "staff", "is_manager": False}
    assert client.get("/api/me", headers=ADRIAN).json()["is_manager"] is True
    # No proxy headers: run directly (development), acts as a manager
    assert client.get("/api/me").json() == {"name": None, "role": "manager", "is_manager": True}
    # A role header alone, without a signed-in user, gives nothing extra
    assert client.get("/api/me", headers={"X-Staff-Role": "staff"}).json()["name"] is None


def test_staff_cannot_change_prices_or_counts(client, fake):
    bread = {"ean13": "6161000000064", "name": "Bread 400 g", "price_kes": 70}
    r = client.put("/api/products/bread-400g", json=bread, headers=MARY)
    assert r.status_code == 403 and "Only managers" in r.json()["detail"]
    assert client.post("/api/discrepancies/7/count", json={"counted_qty": 4}, headers=MARY).status_code == 403
    assert fake.counts == [] and all(p["product_id"] != "bread-400g" for p in fake.products)

    assert client.put("/api/products/bread-400g", json=bread, headers=ADRIAN).status_code == 200
    assert fake.products[-1]["changed_by"] == "adrian"
    r = client.post("/api/discrepancies/7/count", json={"counted_qty": 4, "counted_by": "someone else"}, headers=ADRIAN)
    assert r.status_code == 200 and fake.counts == [{"counted_qty": 4, "counted_by": "adrian"}]


def test_staff_restock_and_exit_record_the_signed_in_name(client, fake):
    r = client.post("/api/restocks", json={"ean13": "6161000000026", "qty": 2, "scan_id": "scan-abcdef01"}, headers=MARY)
    assert r.status_code == 200 and fake.restocks[-1]["recorded_by"] == "mary"
    done = client.post("/api/exit/S2", json={"checked_by": "typed name"}, headers=MARY)
    assert done.status_code == 200 and done.json()["exited_by"] == "mary"


def test_without_proxy_a_name_is_needed(client):
    assert client.post("/api/exit/S2", json={}).status_code == 422
    assert client.post("/api/exit/S2", json={"checked_by": "Mary"}).json()["exited_by"] == "Mary"
