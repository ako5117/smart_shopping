def test_online_orders_to_pack(client):
    orders = client.get("/api/orders").json()
    assert [(o["sale_id"], o["customer_name"], o["stage"]) for o in orders] == [
        ("WEB-00000000000000A1", "Otieno", "ready"), ("WEB-00000000000000B2", "Jane", "packing")]  # oldest first
    jane = orders[1]
    assert jane["total"] == 2 * 65 + 180 and jane["item_count"] == 3
    # Where to find each item: the shelf zones from the shelf catalogue.
    assert [(i["name"], i["qty"], i["zones"]) for i in jane["items"]] == [("Milk 500 ml", 2, ["B1"]), ("Sugar 1 kg", 1, ["A2"])]
    assert orders[0]["items"][0]["zones"] == []  # not on a sensor shelf


def test_mark_ready_records_who(client, fake):
    r = client.post("/api/orders/WEB-00000000000000B2/ready", json={}, headers={"X-Staff-User": "mary", "X-Staff-Role": "staff"})
    assert r.status_code == 200 and r.json()["ready_by"] == "mary"
    assert client.get("/api/orders").json()[1]["stage"] == "ready"
    assert client.post("/api/orders/WEB-00000000000000B2/ready", json={}).status_code == 422  # not signed in: needs a name
    assert client.post("/api/orders/NOPE/ready", json={"packed_by": "john"}).status_code == 404


def test_orders_page_and_inventory_down(client, fake):
    assert "Online orders" in client.get("/orders").text
    fake.down = True
    assert client.get("/api/orders").status_code == 502


def test_exit_check_shows_the_price_paid(client, fake):
    fake.products.append({"product_id": "bread-400g", "ean13": "6161000000064", "name": "Bread 400 g", "price_kes": 90})
    fake.sales.append(fake.online[1])
    sale = client.get("/api/exit/lookup", params={"code": "000000A1"}).json()
    assert (sale["channel"], sale["customer_name"]) == ("online", "Otieno")
    assert sale["items"][0]["price_kes"] == 70  # the price on the order, not today's price
