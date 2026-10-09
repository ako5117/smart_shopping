"""Stock counts: how a store without shelf sensors keeps its stock right (and a hand check for one with them)."""
import pytest

from inventory.service import Conflict, Item, NotFound
from tests.conftest import EAN, MILK, STORE, SUGAR
from tests.test_api import client


def test_count_sets_stock_and_keeps_the_difference_in_the_ledger(inv):
    inv.restock(STORE, EAN[SUGAR], 10, "s1")
    r = inv.count(STORE, EAN[SUGAR], 7, "c1", "mary")
    assert (r["product_id"], r["was"], r["counted"], r["adjusted_by"], r["stock"]) == (SUGAR, 10, 7, -3, 7)
    entry = inv.history(STORE, SUGAR)[0]
    assert (entry["entry_type"], entry["qty_change"], entry["recorded_by"]) == ("adjustment", -3, "mary")
    assert "7 counted" in entry["reason"]


def test_count_can_find_more_than_the_ledger(inv):
    inv.restock(STORE, EAN[MILK], 2, "s1")
    assert inv.count(STORE, EAN[MILK], 5, "c1")["adjusted_by"] == 3
    assert inv.stock(STORE, MILK) == 5


def test_a_matching_count_changes_nothing_but_is_kept(inv):
    inv.restock(STORE, EAN[SUGAR], 4, "s1")
    r = inv.count(STORE, EAN[SUGAR], 4, "c1", "john")
    assert r["adjusted_by"] == 0 and [e["entry_type"] for e in inv.history(STORE, SUGAR)] == ["restock"]
    assert [(c["count_id"], c["counted_qty"], c["counted_by"]) for c in inv.counts(STORE)] == [("c1", 4, "john")]


def test_resending_a_count_changes_nothing_even_after_a_sale(inv):
    """The phone re-sent the count after a sale went through: the sale must not be undone."""
    inv.restock(STORE, EAN[SUGAR], 10, "s1")
    first = inv.count(STORE, EAN[SUGAR], 10, "c1")
    inv.create_sale("SALE-1", STORE, [Item(SUGAR, 1)])
    inv.commit_sale("SALE-1", "REF")
    again = inv.count(STORE, EAN[SUGAR], 10, "c1")
    assert again["adjusted_by"] == first["adjusted_by"] == 0 and again["stock"] == 9
    with pytest.raises(Conflict):
        inv.count(STORE, EAN[SUGAR], 3, "c1")  # same count id, different number


def test_count_rules(inv):
    with pytest.raises(NotFound):
        inv.count(STORE, "6161999999999", 3, "c1")
    with pytest.raises(ValueError):
        inv.count(STORE, EAN[SUGAR], -1, "c2")
    assert inv.count(STORE, EAN[SUGAR], 0, "c3")["stock"] == 0  # none left: fine


def test_count_over_http():
    c = client()
    c.post("/restocks", json={"store_id": "001", "ean13": "6161000000040", "qty": 12, "scan_id": "s1"})
    r = c.post("/counts", json={"store_id": "001", "ean13": "6161000000040", "counted_qty": 9, "count_id": "k1"},
               headers={"X-Staff-User": "mary"})
    assert r.status_code == 200 and (r.json()["adjusted_by"], r.json()["counted_by"]) == (-3, "mary")
    assert c.get("/stock/001").json() == {"milk-500ml": 9}
    assert [x["name"] for x in c.get("/counts/001").json()] == ["Milk 500 ml"]
    assert c.post("/counts", json={"store_id": "001", "ean13": "6161000000040", "counted_qty": -1,
                                   "count_id": "k2"}).status_code == 422
    assert c.post("/counts", json={"store_id": "001", "ean13": "6161999999999", "counted_qty": 1,
                                   "count_id": "k3"}).status_code == 404
