from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from inventory.api import create_app
from inventory.returns import Returns
from inventory.service import Conflict, Item
from tests.conftest import EAN, FLOUR, MILK, STORE, SUGAR, fresh_db


def paid_sale(inv, sale_id="WEB-1", items=((SUGAR, 3), (MILK, 2)), discount=0):
    inv.upsert_product(SUGAR, EAN[SUGAR], "Sugar 1 kg", price_kes=180)
    inv.upsert_product(MILK, EAN[MILK], "Milk 500 ml", price_kes=65, perishable=True)
    inv.upsert_product(FLOUR, EAN[FLOUR], "Maize flour 2 kg", price_kes=210)
    for pid, _ in items:
        inv.restock(STORE, EAN[pid], 10, f"s-{pid}-{sale_id}")
    inv.create_sale(sale_id, STORE, [Item(p, q) for p, q in items], channel="online", loyalty_discount_kes=discount)
    inv.commit_sale(sale_id, f"PAY-{sale_id}")


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def returns(inv, clock):
    return Returns(inv, days=14, clock=clock)


def test_only_non_perishables_can_go_back(inv, returns):
    paid_sale(inv)
    r = returns.returnable("WEB-1")
    items = {i["product_id"]: i for i in r["items"]}
    assert r["open"] and r["days"] == 14
    assert (items[SUGAR]["returnable"], items[SUGAR]["unit_refund_kes"]) == (3, 180)
    assert (items[MILK]["returnable"], items[MILK]["perishable"]) == (0, True)
    assert "perishables" in items[MILK]["why_not"]
    with pytest.raises(Conflict, match="perishables"):
        returns.request("WEB-1", [{"product_id": MILK, "qty": 1}], "Changed my mind", "customer", True)


def test_unopened_and_a_reason_are_needed(inv, returns):
    paid_sale(inv)
    with pytest.raises(ValueError, match="unopened"):
        returns.request("WEB-1", [{"product_id": SUGAR, "qty": 1}], "Changed my mind", "customer", False)
    with pytest.raises(ValueError, match="why"):
        returns.request("WEB-1", [{"product_id": SUGAR, "qty": 1}], "", "customer", True)
    with pytest.raises(ValueError, match="wasn't in this order"):
        returns.request("WEB-1", [{"product_id": FLOUR, "qty": 1}], "Wrong item", "customer", True)


def test_request_approve_refund(inv, returns):
    paid_sale(inv)
    before = inv.stock(STORE, SUGAR)
    r = returns.request("WEB-1", [{"product_id": SUGAR, "qty": 2}], "Bought too much", "customer", True)
    assert (r["status"], r["total_kes"], r["started_by"]) == ("requested", 360, "customer")
    assert inv.stock(STORE, SUGAR) == before  # not back on the shelf until checked
    assert {i["product_id"]: i["returnable"] for i in returns.returnable("WEB-1")["items"]}[SUGAR] == 1  # 2 are waiting
    with pytest.raises(Conflict, match="Only 1"):
        returns.request("WEB-1", [{"product_id": SUGAR, "qty": 2}], "Bought too much", "customer", True)
    with pytest.raises(ValueError, match="Check the items"):
        returns.approve(r["return_id"], "adrian", checked=False)
    with pytest.raises(Conflict, match="Approve the return"):
        returns.refunded(r["return_id"], "adrian", "mpesa", "QX1")
    a = returns.approve(r["return_id"], "adrian", checked=True, note="Sealed")
    assert (a["status"], a["refund_kes"], a["decided_by"]) == ("approved", 360, "adrian")
    assert inv.stock(STORE, SUGAR) == before + 2
    with pytest.raises(Conflict):
        returns.approve(r["return_id"], "adrian", checked=True)  # stock goes back once
    with pytest.raises(ValueError, match="reference"):
        returns.refunded(r["return_id"], "adrian", "mpesa", "")
    done = returns.refunded(r["return_id"], "adrian", "mpesa", "SKR12AB34")
    assert (done["status"], done["refund_method"], done["refund_ref"]) == ("refunded", "mpesa", "SKR12AB34")
    assert {i["product_id"]: i["returnable"] for i in returns.returnable("WEB-1")["items"]}[SUGAR] == 1


def test_reject_frees_the_items(inv, returns):
    paid_sale(inv)
    r = returns.request("WEB-1", [{"product_id": SUGAR, "qty": 3}], "Changed my mind", "customer", True)
    with pytest.raises(ValueError):
        returns.reject(r["return_id"], "adrian", "")
    x = returns.reject(r["return_id"], "adrian", "The packet was opened")
    assert (x["status"], x["note"]) == ("rejected", "The packet was opened")
    assert {i["product_id"]: i["returnable"] for i in returns.returnable("WEB-1")["items"]}[SUGAR] == 3
    assert [r["status"] for r in returns.list(STORE, ["rejected"])] == ["rejected"]
    assert returns.list(STORE) == []


def test_fourteen_days_from_payment(inv, returns, clock):
    paid_sale(inv)
    clock.now += timedelta(days=14, hours=1)
    r = returns.returnable("WEB-1")
    assert not r["open"] and "14-day return period ended" in r["reason"]
    with pytest.raises(Conflict, match="return period"):
        returns.request("WEB-1", [{"product_id": SUGAR, "qty": 1}], "Late", "customer", True)
    assert Returns(inv, days=30, clock=clock).returnable("WEB-1")["open"]  # RETURN_DAYS can be changed


def test_points_discount_is_shared_and_unpaid_sales_cannot_return(inv, returns):
    paid_sale(inv, items=((SUGAR, 2),), discount=60)  # 360 of goods, KES 60 off with points
    assert returns.returnable("WEB-1")["items"][0]["unit_refund_kes"] == 150
    inv.create_sale("WEB-2", STORE, [Item(SUGAR, 1)], channel="online")
    assert "paid" in returns.returnable("WEB-2")["reason"]


def test_http(clock):
    c = TestClient(create_app(fresh_db(), return_days=14, clock=clock))
    for pid, name, price, per in ((SUGAR, "Sugar 1 kg", 180, False), (MILK, "Milk 500 ml", 65, True)):
        c.put(f"/products/{pid}", json={"product_id": pid, "ean13": EAN[pid], "name": name, "price_kes": price, "perishable": per})
        c.post("/restocks", json={"store_id": STORE, "ean13": EAN[pid], "qty": 5, "scan_id": f"scan-{pid}"})
    assert {p["product_id"]: p["perishable"] for p in c.get("/products").json()} == {SUGAR: False, MILK: True}
    c.put(f"/products/{MILK}", json={"product_id": MILK, "ean13": EAN[MILK], "name": "Milk 500 ml"})  # left out: kept
    assert {p["product_id"]: p["perishable"] for p in c.get("/products").json()}[MILK] is True
    c.post("/sales", json={"sale_id": "SG-1", "store_id": STORE, "items": [{"product_id": SUGAR, "qty": 2}]})
    c.post("/sales/SG-1/commit", json={"payment_ref": "R1"})
    assert c.get("/sales/SG-1").json()["paid_at"]
    assert c.get("/sales/SG-1/returnable").json()["open"]
    r = c.post("/returns", json={"sale_id": "SG-1", "lines": [{"product_id": SUGAR, "qty": 1}], "reason": "Not needed",
                                 "unopened": True}, headers={"X-Staff-User": "mary"})
    assert r.status_code == 201 and r.json()["started_by"] == "mary"
    rid = r.json()["return_id"]
    assert [x["return_id"] for x in c.get("/returns", params={"store_id": STORE}).json()] == [rid]
    assert c.post(f"/returns/{rid}/approve", json={"checked": True}, headers={"X-Staff-User": "adrian"}).json()["decided_by"] == "adrian"
    assert c.post(f"/returns/{rid}/refunded", json={"method": "cash"}).json()["status"] == "refunded"
    assert c.post(f"/returns/{rid}/reject", json={"note": "too late"}).status_code == 409
    assert c.get("/returns/RET-NOPE").status_code == 404
