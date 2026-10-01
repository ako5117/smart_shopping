from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from inventory.api import create_app
from inventory.service import HOLD_MINUTES, Conflict, Item, NotEnoughStock
from tests.conftest import EAN, FLOUR, MILK, STORE, SUGAR


def test_category_is_kept_unless_given(inv):
    inv.upsert_product(MILK, EAN[MILK], "Milk 500 ml", 65, category="Dairy")
    inv.upsert_product(MILK, EAN[MILK], "Milk 500 ml", 70)  # e.g. the price editor, which doesn't send a category
    [p] = [p for p in inv.products() if p["product_id"] == MILK]
    assert (p["category"], p["price_kes"]) == ("Dairy", 70)
    inv.upsert_product(MILK, EAN[MILK], "Milk 500 ml", category="")
    assert [p for p in inv.products() if p["product_id"] == MILK][0]["category"] == ""


def test_unpaid_orders_hold_stock_for_a_while(inv, db):
    inv.restock(STORE, EAN[MILK], 10, "s1")
    inv.create_sale("WEB-1", STORE, [Item(MILK, 3)], channel="online")
    inv.create_sale("SG-1", STORE, [Item(MILK, 2)])
    inv.create_sale("SG-2", STORE, [Item(MILK, 1)])
    inv.cancel_sale("SG-2")
    assert inv.availability(STORE) == {MILK: {"stock": 10, "held": 5}}

    inv.commit_sale("WEB-1", "REF1")  # paid: out of stock, no longer held
    assert inv.availability(STORE) == {MILK: {"stock": 7, "held": 2}}

    later = datetime.now(timezone.utc) + timedelta(minutes=HOLD_MINUTES + 1)
    assert inv.availability(STORE, now=later) == {MILK: {"stock": 7, "held": 0}}


def test_online_order_is_refused_when_not_enough_is_available(inv):
    inv.restock(STORE, EAN[MILK], 5, "s1")
    inv.restock(STORE, EAN[SUGAR], 1, "s2")
    inv.create_sale("SG-1", STORE, [Item(MILK, 2)])  # a Scan & Go shopper is paying for 2
    with pytest.raises(NotEnoughStock) as e:
        inv.create_sale("WEB-1", STORE, [Item(MILK, 4), Item(SUGAR, 1), Item(FLOUR, 1)], channel="online",
                        check_stock=True)
    assert sorted(e.value.short, key=lambda s: s["product_id"]) == [
        {"product_id": FLOUR, "requested": 1, "available": 0},
        {"product_id": MILK, "requested": 4, "available": 3}]
    with pytest.raises(Exception):
        inv.sale("WEB-1")  # nothing written

    sale = inv.create_sale("WEB-2", STORE, [Item(MILK, 3), Item(SUGAR, 1)], channel="online",
                           customer_name=" Jane ", check_stock=True)
    assert (sale["channel"], sale["customer_name"], sale["status"]) == ("online", "Jane", "pending_payment")
    with pytest.raises(NotEnoughStock):  # WEB-2 now holds the rest
        inv.create_sale("WEB-3", STORE, [Item(MILK, 1)], channel="online", check_stock=True)


def test_an_expired_hold_frees_the_stock(inv, db):
    inv.restock(STORE, EAN[MILK], 2, "s1")
    inv.create_sale("WEB-1", STORE, [Item(MILK, 2)], channel="online", check_stock=True)
    old = (datetime.now(timezone.utc) - timedelta(minutes=HOLD_MINUTES + 1)).isoformat()
    with db.tx() as c:
        c.execute("UPDATE sales SET created_at = ? WHERE sale_id = 'WEB-1'", (old,))
    assert inv.create_sale("WEB-2", STORE, [Item(MILK, 2)], channel="online", check_stock=True)


def test_ready_for_collection_then_collected(inv):
    inv.restock(STORE, EAN[MILK], 5, "s1")
    inv.create_sale("WEB-1", STORE, [Item(MILK, 1)], channel="online", customer_name="Jane")
    inv.create_sale("SG-1", STORE, [Item(MILK, 1)])
    with pytest.raises(Conflict):
        inv.mark_ready("WEB-1", "mary")  # not paid yet
    inv.commit_sale("WEB-1", "REF1")
    inv.commit_sale("SG-1", "REF2")
    with pytest.raises(Conflict):
        inv.mark_ready("SG-1", "mary")  # Scan & Go orders leave with the customer

    first = inv.mark_ready("WEB-1", "mary")
    again = inv.mark_ready("WEB-1", "john")
    assert first["ready_by"] == again["ready_by"] == "mary" and first["ready_at"] == again["ready_at"]

    assert [s["sale_id"] for s in inv.recent_sales(STORE, channel="online", status="paid", uncollected=True)] == ["WEB-1"]
    inv.record_exit("WEB-1", "john")
    assert inv.recent_sales(STORE, channel="online", uncollected=True) == []
    with pytest.raises(Conflict):
        inv.mark_ready("WEB-1", "mary")


def test_online_orders_over_http(db):
    c = TestClient(create_app(db))
    c.put("/products/milk-500ml", json={"product_id": "milk-500ml", "ean13": EAN[MILK], "name": "Milk 500 ml",
                                        "price_kes": 65, "category": "Dairy"})
    assert c.get("/products").json()[0]["category"] == "Dairy"
    c.post("/restocks", json={"store_id": STORE, "ean13": EAN[MILK], "qty": 2, "scan_id": "s1"})

    order = {"sale_id": "WEB-1", "store_id": STORE, "items": [{"product_id": MILK, "qty": 3}],
             "channel": "online", "customer_name": "Jane", "check_stock": True}
    r = c.post("/sales", json=order)
    assert r.status_code == 409
    assert r.json()["detail"]["short"] == [{"product_id": MILK, "requested": 3, "available": 2}]

    assert c.post("/sales", json={**order, "items": [{"product_id": MILK, "qty": 2}]}).status_code == 201
    assert c.get(f"/availability/{STORE}").json() == {MILK: {"stock": 2, "held": 2}}
    assert c.post("/sales", json={**order, "channel": "phone"}).status_code == 422

    c.post("/sales/WEB-1/commit", json={"payment_ref": "REF1"})
    r = c.post("/sales/WEB-1/ready", json={}, headers={"X-Staff-User": "mary"})
    assert r.status_code == 200 and r.json()["ready_by"] == "mary"
    listed = c.get("/sales", params={"store_id": STORE, "channel": "online", "uncollected": True}).json()
    assert [(s["sale_id"], s["customer_name"]) for s in listed] == [("WEB-1", "Jane")]
    assert c.post("/sales/NOPE/ready", json={"ready_by": "x"}).status_code == 404


def test_delivery_orders_carry_their_fee(inv):
    inv.restock(STORE, EAN[MILK], 5, "s1")
    sale = inv.create_sale("WEB-1", STORE, [Item(MILK, 1)], channel="online", fulfilment="delivery", delivery_fee_kes=150)
    assert (sale["fulfilment"], sale["delivery_fee_kes"]) == ("delivery", 150)
    assert inv.create_sale("WEB-2", STORE, [Item(MILK, 1)], channel="online")["fulfilment"] == "collect"
    for bad in ({"channel": "in_store", "fulfilment": "delivery"},  # Scan & Go shoppers carry their own
                {"channel": "online", "delivery_fee_kes": 100},  # a fee without a delivery
                {"channel": "online", "fulfilment": "drone"}):
        with pytest.raises(ValueError):
            inv.create_sale("WEB-X", STORE, [Item(MILK, 1)], **bad)
