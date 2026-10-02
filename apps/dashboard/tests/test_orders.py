def test_online_orders_to_pack(client):
    body = client.get("/api/orders").json()
    assert body["deliveries_on"] is False and body["out"] == []
    orders = body["orders"]
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
    assert client.get("/api/orders").json()["orders"][1]["stage"] == "ready"
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


def delivery_order(fake, dispatch_fake, sale_id="WEB-00000000000000C3", status="unassigned", rider=None):
    sale = {"sale_id": sale_id, "store_id": "001", "status": "paid", "channel": "online", "fulfilment": "delivery",
            "delivery_fee_kes": 150, "customer_name": "Wanjiru", "payment_ref": "REF7",
            "created_at": "2026-10-01T09:45:00+00:00", "ready_at": None, "ready_by": None, "exited_at": None,
            "items": [{"product_id": "sugar-1kg", "qty": 2, "unit_price_kes": 180}]}
    (fake.online if status in ("unassigned", "assigned") else fake.delivered).append(sale)
    dispatch_fake.deliveries[sale_id] = {"sale_id": sale_id, "status": status, "rider_id": rider, "area": "Kilimani",
                                         "address": "Hse 4, Argwings Kodhek Rd", "notes": "", "fee_kes": 150,
                                         "customer_phone": "0712345678", "fail_reason": "", "locked": False}
    return sale


def test_delivery_orders_show_where_and_who(delivery_client, fake, dispatch_fake):
    delivery_order(fake, dispatch_fake)
    delivery_order(fake, dispatch_fake, "WEB-00000000000000D4", status="picked_up", rider="otieno")
    body = delivery_client.get("/api/orders").json()
    assert body["deliveries_on"] is True and body["dispatch_error"] is None
    assert [r["rider_id"] for r in body["riders"]] == ["otieno", "akinyi"]
    c3 = next(o for o in body["orders"] if o["sale_id"] == "WEB-00000000000000C3")
    assert (c3["delivery"]["area"], c3["delivery"]["status"], c3["total"]) == ("Kilimani", "unassigned", 2 * 180 + 150)
    assert [o["sale_id"] for o in body["out"]] == ["WEB-00000000000000D4"]
    assert body["out"][0]["delivery"]["rider_id"] == "otieno"
    collect = next(o for o in body["orders"] if o["sale_id"] == "WEB-00000000000000B2")
    assert collect["delivery"] is None


def test_assign_and_hand_over_record_who(delivery_client, fake, dispatch_fake):
    delivery_order(fake, dispatch_fake)
    staff = {"X-Staff-User": "mary", "X-Staff-Role": "staff"}
    r = delivery_client.post("/api/orders/WEB-00000000000000C3/hand-over", json={}, headers=staff)
    assert r.status_code == 409 and "no rider" in r.json()["detail"]
    delivery_client.post("/api/orders/WEB-00000000000000C3/assign", json={"rider_id": "otieno"}, headers=staff)
    delivery_client.post("/api/orders/WEB-00000000000000C3/unassign", json={}, headers=staff)
    delivery_client.post("/api/orders/WEB-00000000000000C3/assign", json={"rider_id": "otieno"}, headers=staff)
    assert delivery_client.post("/api/orders/WEB-00000000000000C3/hand-over", json={}, headers=staff).json()["status"] == "picked_up"
    delivery_client.post("/api/orders/WEB-00000000000000C3/retry", json={}, headers=staff)
    assert [(a, b) for _, a, b in dispatch_fake.calls] == [
        ("hand-over", {"by": "mary"}), ("assign", {"rider_id": "otieno", "by": "mary"}), ("release", {"by": "mary"}),
        ("assign", {"rider_id": "otieno", "by": "mary"}), ("hand-over", {"by": "mary"}), ("retry", {"by": "mary"})]


def test_dispatch_down_still_shows_orders(delivery_client, fake, dispatch_fake):
    dispatch_fake.down = True
    body = delivery_client.get("/api/orders").json()
    assert len(body["orders"]) == 2 and "unavailable" in body["dispatch_error"]
    assert delivery_client.post("/api/orders/WEB-00000000000000B2/assign", json={"rider_id": "otieno"},
                                headers={"X-Staff-User": "mary"}).status_code == 502


def test_no_dispatch_service_no_delivery_actions(client):
    assert client.post("/api/orders/WEB-00000000000000B2/assign", json={"rider_id": "otieno"},
                       headers={"X-Staff-User": "mary"}).status_code == 404
