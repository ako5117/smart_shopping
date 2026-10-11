"""Discounts: managers put a product on offer (percent or KES off); every channel charges the offer price."""
from datetime import datetime, timedelta, timezone

import pytest

from inventory.service import Conflict, Item, NotFound, discounted
from tests.conftest import STORE
from tests.test_api import client

SUGAR = "sugar-1kg"


@pytest.fixture
def shop(inv):
    inv.upsert_product(SUGAR, "6161000000026", "Sugar 1 kg", 180)
    inv.restock(STORE, "6161000000026", 10, "s1")
    return inv


def iso(**kw):
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat()


@pytest.mark.parametrize("price,kind,value,want", [(180, "percent", 10, 162), (65, "percent", 15, 55), (180, "amount", 30, 150),
                                                   (5, "percent", 90, 1), (2, "amount", 1, 1)])
def test_offer_prices_round_to_whole_shillings(price, kind, value, want):
    assert discounted(price, kind, value) == want


def test_a_live_offer_is_charged_and_the_receipt_keeps_the_shelf_price(shop):
    d = shop.add_discount(SUGAR, "percent", 10, "adrian")
    assert d["state"] == "live" and d["sale_price_kes"] == 162
    p = next(p for p in shop.products() if p["product_id"] == SUGAR)
    assert (p["price_kes"], p["sale_price_kes"], p["discount"]["kind"]) == (180, 162, "percent")
    sale = shop.create_sale("S1", STORE, [Item(SUGAR, 2)])
    assert sale["items"] == [{"product_id": SUGAR, "qty": 2, "unit_price_kes": 162, "list_price_kes": 180}]
    shop.end_discount(d["discount_id"], "adrian")
    assert shop.create_sale("S2", STORE, [Item(SUGAR, 1)])["items"][0]["unit_price_kes"] == 180
    assert shop.sale("S1")["items"][0]["unit_price_kes"] == 162  # the earlier sale keeps its price


def test_scheduled_and_expired_offers(shop):
    later = shop.add_discount(SUGAR, "amount", 20, "adrian", starts_at=iso(days=1), ends_at=iso(days=3))
    assert later["state"] == "scheduled"
    assert next(p for p in shop.products() if p["product_id"] == SUGAR)["sale_price_kes"] is None
    with pytest.raises(Conflict):
        shop.add_discount(SUGAR, "percent", 5, "adrian", starts_at=iso(days=2))  # overlaps the scheduled one
    now = shop.add_discount(SUGAR, "percent", 5, "adrian", ends_at=iso(hours=1))  # before it: fine
    assert now["state"] == "live"
    assert {d["discount_id"] for d in shop.discounts()} == {later["discount_id"], now["discount_id"]}


def test_offer_rules(shop):
    with pytest.raises(ValueError):
        shop.add_discount(SUGAR, "percent", 95, "adrian")
    with pytest.raises(ValueError):
        shop.add_discount(SUGAR, "amount", 180, "adrian")  # free isn't an offer
    with pytest.raises(ValueError):
        shop.add_discount(SUGAR, "percent", 10, "adrian", ends_at=iso(hours=-1))
    with pytest.raises(NotFound):
        shop.add_discount("nope", "percent", 10, "adrian")
    d = shop.add_discount(SUGAR, "percent", 10, "adrian")
    shop.end_discount(d["discount_id"], "mary")
    with pytest.raises(Conflict):
        shop.end_discount(d["discount_id"], "mary")
    assert shop.discounts() == [] and shop.discounts(include_past=True)[0]["ended_by"] == "mary"


def test_discounts_over_http():
    c = client()
    c.put("/products/milk-500ml", json={"product_id": "milk-500ml", "ean13": "6161000000040", "name": "Milk 500 ml", "price_kes": 65})
    r = c.post("/discounts", json={"product_id": "milk-500ml", "kind": "amount", "value": 5}, headers={"X-Staff-User": "adrian"})
    assert r.status_code == 201 and (r.json()["sale_price_kes"], r.json()["created_by"]) == (60, "adrian")
    assert c.get("/products").json()[0]["sale_price_kes"] == 60
    assert c.post("/discounts", json={"product_id": "milk-500ml", "kind": "percent", "value": 10}).status_code == 409
    assert c.post("/discounts", json={"product_id": "milk-500ml", "kind": "half", "value": 10}).status_code == 422
    assert c.post(f"/discounts/{r.json()['discount_id']}/end", json={}).json()["state"] == "ended"
    assert c.get("/discounts").json() == []
