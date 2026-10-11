from datetime import timedelta

from customers.mailer import OutboxMailer
from customers.service import Customers, Settings
from tests.conftest import signed_up


def member(customers, db):
    return signed_up(customers, db)["customer"]["customer_id"]


def test_one_point_per_kes_100_once_paid(customers, db, inventory):
    cid = member(customers, db)
    customers.start_order(cid, "WEB-1", 1290, False)
    assert customers.balance(cid) == 0
    inventory.add("WEB-1", status="pending_payment", items=((645, 2),), fee=290)
    customers.settle()
    assert customers.order("WEB-1")["status"] == "pending"
    inventory.sales["WEB-1"]["status"] = "paid"
    assert customers.settle() == 1
    assert (customers.balance(cid), customers.order("WEB-1")["earned_points"]) == (12, 12)  # goods only, not the fee
    customers.settle()
    assert customers.balance(cid) == 12  # earned once


def test_spend_points_in_blocks(customers, db, inventory):
    cid = member(customers, db)
    db.one("SELECT 1")
    with db.tx() as c:
        c.execute("INSERT INTO points (customer_id, sale_id, kind, points, created_at) VALUES (?, 'OLD', 'earn', 250, 'x')", (cid,))
    assert customers.loyalty(cid)["worth_kes"] == 100  # 2 blocks of 100 points, KES 50 each
    o = customers.start_order(cid, "WEB-1", 800, True)
    assert (o["redeemed_points"], o["discount_kes"], customers.balance(cid)) == (200, 100, 50)
    # Paid: earns on what was actually paid for goods (800 - 100)
    inventory.add("WEB-1", items=((800, 1),), discount=100)
    customers.settle()
    assert customers.balance(cid) == 50 + 7


def test_discount_never_covers_the_whole_order(customers, db):
    cid = member(customers, db)
    with db.tx() as c:
        c.execute("INSERT INTO points (customer_id, sale_id, kind, points, created_at) VALUES (?, 'OLD', 'earn', 1000, 'x')", (cid,))
    assert customers.start_order(cid, "WEB-1", 120, True)["discount_kes"] == 100  # 2 blocks; 3 would be 150 > 119
    assert customers.start_order(cid, "WEB-2", 50, True)["discount_kes"] == 0
    assert customers.start_order(cid, "WEB-1", 120, True)["discount_kes"] == 100  # repeating changes nothing
    assert customers.balance(cid) == 800


def test_points_come_back_when_the_order_does_not_happen(customers, db, inventory, clock):
    cid = member(customers, db)
    with db.tx() as c:
        c.execute("INSERT INTO points (customer_id, sale_id, kind, points, created_at) VALUES (?, 'OLD', 'earn', 300, 'x')", (cid,))
    customers.start_order(cid, "WEB-1", 60, True)  # one block each
    customers.start_order(cid, "WEB-2", 60, True)  # one block each
    customers.start_order(cid, "WEB-3", 60, True)  # one block each
    assert customers.balance(cid) == 0
    customers.cancel_order("WEB-1")  # the shop couldn't record it
    inventory.add("WEB-2", status="cancelled")  # expired unpaid
    customers.settle()
    assert customers.balance(cid) == 200
    customers.settle()
    customers.cancel_order("WEB-1")
    assert customers.balance(cid) == 200  # given back once
    clock.now += timedelta(hours=25)  # WEB-3 never reached the Inventory Service
    customers.settle()
    assert customers.balance(cid) == 300
    assert [o["sale_id"] for o in customers.orders(cid)] == []


def test_inventory_down_leaves_orders_waiting(customers, db, inventory):
    cid = member(customers, db)
    customers.start_order(cid, "WEB-1", 500, False)
    inventory.add("WEB-1")
    inventory.down = True
    assert customers.settle() == 0
    inventory.down = False
    assert customers.settle() == 1 and customers.balance(cid) == 5


def test_loyalty_switched_off(db, inventory, clock):
    off = Customers(db, OutboxMailer(db), Settings(loyalty_enabled=False), inventory, clock)
    cid = member(off, db)
    with db.tx() as c:
        c.execute("INSERT INTO points (customer_id, sale_id, kind, points, created_at) VALUES (?, 'OLD', 'earn', 300, 'x')", (cid,))
    assert off.start_order(cid, "WEB-1", 1000, True)["discount_kes"] == 0
    inventory.add("WEB-1", items=((1000, 1),))
    off.settle()
    assert off.balance(cid) == 300  # nothing earned or spent; the balance is kept
    assert off.profile(cid)["loyalty"]["enabled"] is False and off.orders(cid)[0]["status"] == "paid"


def test_dashboard_sees_members_not_personal_details(customers, db, inventory):
    cid = member(customers, db)
    customers.start_order(cid, "WEB-1", 2000, False)
    inventory.add("WEB-1", items=((2000, 1),))
    customers.settle()
    [m] = customers.members()
    assert (m["name"], m["email"], m["points"], m["orders"]) == ("Jane Wanjiru", "j***@example.com", 20, 1)
    assert "dob" not in m and "gender" not in m and "phone" not in m
    s = customers.summary()
    assert (s["members"], s["points_outstanding"], s["points_earned"]) == (1, 20, 20)
