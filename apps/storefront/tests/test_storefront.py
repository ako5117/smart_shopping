from datetime import timedelta

from storefront.shelf import picked_up, read_recent_lines
from tests.conftest import NOW, shelf_event


def catalogue(client):
    return {p["product_id"]: p for p in client.get("/api/catalogue").json()["products"]}


def order(client, items, name="Jane", phone="0712345678"):
    return client.post("/api/orders", json={"name": name, "phone": phone, "items": items})


def test_catalogue_shows_live_availability_and_substitutes(client):
    c = client.get("/api/catalogue").json()
    assert c["categories"] == ["Dairy", "Sugar"]
    assert c["shelf_live"] is False  # no shelf events yet
    p = {x["product_id"]: x for x in c["products"]}
    assert "bread-400g" not in p  # no price: not sold online
    assert (p["milk-500ml"]["status"], p["milk-500ml"]["max"]) == ("in", 9)  # 10 in stock, 1 kept back
    assert (p["sugar-1kg"]["status"], p["sugar-1kg"]["left"]) == ("few", 3)
    assert p["uht-milk-500ml"]["status"] == "out"
    # Same category, in stock, closest price first.
    assert [s["product_id"] for s in p["uht-milk-500ml"]["substitutes"]] == ["milk-500ml", "yoghurt-500ml"]
    assert p["sugar-1kg"]["substitutes"] == []  # nothing else in the category
    assert p["milk-500ml"]["substitutes"] == []  # plenty in stock


def test_items_picked_up_in_the_store_are_not_promised_online(client, shelf_log):
    shelf_event(shelf_log, "pick", "milk-500ml", qty=3, minutes_ago=1)
    shelf_event(shelf_log, "return", "milk-500ml", qty=1, minutes_ago=0.5)  # put one back
    shelf_event(shelf_log, "pick", "milk-500ml", qty=4, minutes_ago=30)  # long gone: paid for, or put back
    shelf_event(shelf_log, "anomaly", None)
    c = client.get("/api/catalogue").json()
    assert c["shelf_live"] is True
    assert {p["product_id"]: p["max"] for p in c["products"]}["milk-500ml"] == 10 - 2 - 1


def test_picked_up_reader(tmp_path):
    log = tmp_path / "events.jsonl"
    shelf_event(log, "pick", "milk-500ml", qty=2)
    shelf_event(log, "moved", "milk-500ml", qty=1)  # put on another shelf: still in the store
    shelf_event(log, "misplaced", "sugar-1kg", qty=1)  # put back without a recent pick: not negative
    with open(log, "a") as f:
        f.write('{"event_type": "pick", "product_id": "half-writ')
    events = read_recent_lines(str(log))
    assert len(events) == 3
    assert picked_up(events, 5, NOW) == {"milk-500ml": 1}
    assert picked_up(events, 5, NOW + timedelta(minutes=10)) == {}
    assert read_recent_lines(str(tmp_path / "missing.jsonl")) == []


def test_order_is_priced_and_held(client, inventory, payments):
    r = order(client, [{"product_id": "milk-500ml", "qty": 2}, {"product_id": "yoghurt-500ml", "qty": 1},
                       {"product_id": "milk-500ml", "qty": 1}])
    assert r.status_code == 201
    o = r.json()
    assert o["order_id"].startswith("WEB-") and len(o["order_id"]) == 20
    assert o["total"] == 3 * 65 + 120
    sale = inventory.sales[o["order_id"]]
    assert (sale["channel"], sale["customer_name"]) == ("online", "Jane")
    assert [(i["product_id"], i["qty"]) for i in sale["items"]] == [("milk-500ml", 3), ("yoghurt-500ml", 1)]
    assert payments.pushes == [{"sale_id": o["order_id"], "phone": "0712345678", "amount": 315}]
    assert catalogue(client)["milk-500ml"]["max"] == 10 - 3 - 1  # held while the customer pays


def test_order_for_more_than_is_available_suggests_substitutes(client, payments):
    r = order(client, [{"product_id": "uht-milk-500ml", "qty": 1}, {"product_id": "sugar-1kg", "qty": 4}])
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Long-life milk 500 ml" in detail["message"] and "Sugar 1 kg" in detail["message"]
    short = {s["product_id"]: s for s in detail["short"]}
    assert (short["sugar-1kg"]["requested"], short["sugar-1kg"]["available"]) == (4, 3)
    assert [s["name"] for s in short["uht-milk-500ml"]["substitutes"]] == ["Milk 500 ml", "Yoghurt 500 ml"]
    assert payments.pushes == []


def test_inventory_has_the_last_word(client, inventory, payments):
    inventory.refuse_online = True
    r = order(client, [{"product_id": "milk-500ml", "qty": 1}])
    assert r.status_code == 409 and r.json()["detail"]["short"][0]["product_id"] == "milk-500ml"
    assert payments.pushes == [] and inventory.sales == {}


def test_order_validation(client, payments):
    assert order(client, [{"product_id": "bread-400g", "qty": 1}]).status_code == 409  # no price
    assert order(client, [{"product_id": "ghost", "qty": 1}]).status_code == 409
    assert order(client, [{"product_id": "milk-500ml", "qty": 21}]).status_code == 422
    assert order(client, [{"product_id": "milk-500ml", "qty": 1}], name="").status_code == 422
    assert order(client, []).status_code == 422
    assert payments.pushes == []


def test_from_payment_to_collection(client, inventory, payments):
    o = order(client, [{"product_id": "milk-500ml", "qty": 2}]).json()
    url = f"/api/orders/{o['order_id']}/payments/{o['payment_id']}"
    assert client.get(url).json()["stage"] == "awaiting_payment"

    payments.settle(o["payment_id"], "paid", receipt="SJT4K2L9QX")
    s = client.get(url).json()
    assert (s["stage"], s["customer_name"], s["mpesa_receipt"], s["phone"]) == ("packing", "Jane", "SJT4K2L9QX", "254712***678")
    assert s["lines"] == [{"product_id": "milk-500ml", "name": "Milk 500 ml", "qty": 2, "price": 65, "line_total": 130}]
    assert catalogue(client)["milk-500ml"]["max"] == 8 - 1  # sold: out of stock, no longer held

    inventory.sales[o["order_id"]]["ready_at"] = NOW.isoformat()
    assert client.get(url).json()["stage"] == "ready"
    inventory.sales[o["order_id"]]["exited_at"] = NOW.isoformat()
    s = client.get(url).json()
    assert s["stage"] == "collected" and s["collected_at"]
    assert client.get(f"/api/orders/{o['order_id']}/qr.svg").headers["content-type"] == "image/svg+xml"


def test_failed_payment_can_be_retried_while_held(client, inventory, payments, clock):
    o = order(client, [{"product_id": "milk-500ml", "qty": 1}]).json()
    payments.settle(o["payment_id"], "cancelled")
    assert client.get(f"/api/orders/{o['order_id']}/payments/{o['payment_id']}").json()["stage"] == "payment_failed"
    r = client.post(f"/api/orders/{o['order_id']}/pay", json={"phone": "0712345678"})
    assert r.status_code == 201 and r.json()["total"] == 65

    clock["now"] = NOW + timedelta(minutes=16)
    r = client.post(f"/api/orders/{o['order_id']}/pay", json={"phone": "0712345678"})
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert inventory.sales[o["order_id"]]["status"] == "cancelled"


def test_failed_payment_request_releases_the_hold(client, inventory, payments):
    payments.fail_push = 500
    r = order(client, [{"product_id": "sugar-1kg", "qty": 3}])
    assert r.status_code == 502
    [sale] = inventory.sales.values()
    assert sale["status"] == "cancelled"
    assert catalogue(client)["sugar-1kg"]["max"] == 3


def test_only_online_orders_are_visible(client, inventory, payments):
    inventory.sales["WEB-0000000000000001"] = {"sale_id": "WEB-0000000000000001", "channel": "in_store",
                                               "status": "pending_payment", "items": []}
    assert client.post("/api/orders/WEB-0000000000000001/pay", json={"phone": "0712345678"}).status_code == 404
    assert client.post("/api/orders/SG-0000000000000001/pay", json={"phone": "0712345678"}).status_code == 404
    o = order(client, [{"product_id": "milk-500ml", "qty": 1}]).json()
    assert client.get(f"/api/orders/{o['order_id']}/payments/nope").status_code == 404


def test_store_unreachable(client, inventory):
    inventory.down = True
    r = client.get("/api/catalogue")
    assert r.status_code == 503 and "try again" in r.json()["detail"]


def test_page_and_store_info(client):
    assert "Online shop" in client.get("/").text
    assert client.get("/api/store").json() == {
        "store_id": "001", "name": "Test Store", "currency": "KES", "hold_minutes": 15, "card": True,
        "delivery": {"available": True, "areas": [{"area": "Kilimani", "fee": 150}, {"area": "Westlands", "fee": 200}]}}
