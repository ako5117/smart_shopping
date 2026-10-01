from datetime import datetime, timedelta, timezone

import pytest

from dispatch.service import MAX_PIN_ATTEMPTS, Conflict, Locked, NotFound, WrongPin, parse_areas
from tests.conftest import STORE, new_delivery


def on_shift(dispatch, *riders):
    for r in riders:
        dispatch.save_rider(r, phone="0722 000 111", on_shift=True)


def test_areas_config():
    assert parse_areas("Kilimani:150, Upper Hill:200,") == {"Kilimani": 150, "Upper Hill": 200}
    for bad in ("", "Kilimani", "Kilimani:cheap", ":150"):
        with pytest.raises(ValueError):
            parse_areas(bad)


def test_new_delivery_waits_for_payment_and_keeps_its_code_private(dispatch, inventory):
    d = new_delivery(dispatch, inventory, paid=False)
    assert (d["status"], d["fee_kes"], d["area"]) == ("awaiting_payment", 150, "Kilimani")
    assert len(d["pin"]) == 4 and d["pin"].isdigit()
    assert "pin" not in dispatch.get("WEB-1") and "pin" not in dispatch.list(STORE, ["awaiting_payment"])[0]
    assert dispatch.create("WEB-1", STORE, "Someone else", "0700000000", "Westlands", "Elsewhere")["pin"] == d["pin"]
    assert dispatch.list(STORE) == []  # not active until paid

    inventory.sales["WEB-1"]["status"] = "paid"
    assert [x["status"] for x in dispatch.list(STORE)] == ["unassigned"]


def test_delivery_details_are_checked(dispatch, inventory):
    with pytest.raises(ValueError):
        new_delivery(dispatch, inventory, area="Mombasa")  # not a delivery area
    with pytest.raises(ValueError):
        new_delivery(dispatch, inventory, address=" ")
    with pytest.raises(ValueError):
        new_delivery(dispatch, inventory, lat=-1.29)  # no longitude
    d = new_delivery(dispatch, inventory, lat=-1.2921, lng=36.7856)
    assert (d["lat"], d["lng"]) == (-1.2921, 36.7856)


def test_unpaid_orders_are_cancelled_or_expire(dispatch, inventory, db):
    new_delivery(dispatch, inventory, "WEB-1", paid=False)
    new_delivery(dispatch, inventory, "WEB-2", paid=False)
    new_delivery(dispatch, inventory, "WEB-3", paid=False)
    inventory.sales["WEB-1"]["status"] = "cancelled"
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with db.tx() as c:
        c.execute("UPDATE deliveries SET created_at = ? WHERE sale_id IN ('WEB-2', 'WEB-3')", (old,))
    del inventory.sales["WEB-3"]  # can't tell: leave it alone
    dispatch.sync_payments()
    assert [dispatch.get(s)["status"] for s in ("WEB-1", "WEB-2", "WEB-3")] == ["cancelled", "cancelled", "awaiting_payment"]


def test_inventory_down_leaves_deliveries_waiting(dispatch, inventory):
    new_delivery(dispatch, inventory, paid=False)
    inventory.sales["WEB-1"]["status"] = "paid"
    inventory.down = True
    assert dispatch.get("WEB-1")["status"] == "awaiting_payment"
    inventory.down = False
    assert dispatch.get("WEB-1")["status"] == "unassigned"


def test_riders(dispatch):
    dispatch.save_rider("otieno")
    assert dispatch.rider("otieno") | {"updated_at": None} == {"rider_id": "otieno", "phone": "", "on_shift": False,
                                                               "updated_at": None}
    dispatch.save_rider("otieno", phone="0722 000 111")
    dispatch.save_rider("otieno", on_shift=True)  # phone kept
    r = dispatch.rider("otieno")
    assert (r["phone"], r["on_shift"]) == ("0722000111", True)
    with pytest.raises(ValueError):
        dispatch.save_rider("otieno", phone="call me")
    with pytest.raises(NotFound):
        dispatch.rider("ghost")


def test_rider_accepts_a_job_first_come_first_served(dispatch, inventory):
    new_delivery(dispatch, inventory)
    dispatch.save_rider("otieno")
    with pytest.raises(Conflict, match="shift"):
        dispatch.claim("WEB-1", "otieno")
    on_shift(dispatch, "otieno", "akinyi")
    d = dispatch.claim("WEB-1", "otieno")
    assert (d["status"], d["rider_id"]) == ("assigned", "otieno")
    with pytest.raises(Conflict, match="Another rider"):
        dispatch.claim("WEB-1", "akinyi")
    assert [r["active_jobs"] for r in dispatch.riders()] == [0, 1]  # akinyi, otieno

    with pytest.raises(Conflict):
        dispatch.release("WEB-1", "akinyi", rider_id="akinyi")  # not hers
    assert dispatch.release("WEB-1", "otieno", rider_id="otieno")["status"] == "unassigned"


def test_staff_assign_and_hand_over(dispatch, inventory):
    new_delivery(dispatch, inventory, "WEB-1", paid=False)
    on_shift(dispatch, "otieno", "akinyi")
    with pytest.raises(Conflict, match="isn't paid"):
        dispatch.assign("WEB-1", "otieno", "mary")
    inventory.sales["WEB-1"]["status"] = "paid"
    with pytest.raises(NotFound):
        dispatch.assign("WEB-1", "ghost", "mary")
    dispatch.assign("WEB-1", "otieno", "mary")
    assert dispatch.assign("WEB-1", "akinyi", "mary")["rider_id"] == "akinyi"  # moved before pickup

    d = dispatch.hand_over("WEB-1", "mary")
    assert (d["status"], d["handed_over_by"]) == ("picked_up", "mary") and d["picked_up_at"]
    assert inventory.exits == [("WEB-1", "mary (to rider akinyi)")]
    with pytest.raises(Conflict, match="out with akinyi"):
        dispatch.assign("WEB-1", "otieno", "mary")
    with pytest.raises(Conflict):
        dispatch.release("WEB-1", "mary")


def test_hand_over_needs_the_inventory_to_agree(dispatch, inventory):
    new_delivery(dispatch, inventory)
    on_shift(dispatch, "otieno")
    dispatch.assign("WEB-1", "otieno", "mary")
    inventory.sales["WEB-1"]["exited_at"] = "earlier"  # e.g. handed to the customer at the counter by mistake
    with pytest.raises(Conflict, match="already left"):
        dispatch.hand_over("WEB-1", "mary")
    assert dispatch.get("WEB-1")["status"] == "assigned"


def out_with_rider(dispatch, inventory, rider="otieno"):
    pin = new_delivery(dispatch, inventory)["pin"]
    on_shift(dispatch, rider)
    dispatch.claim("WEB-1", rider)
    dispatch.hand_over("WEB-1", "mary")
    return pin


def test_delivered_only_with_the_customers_code(dispatch, inventory):
    pin = out_with_rider(dispatch, inventory)
    wrong = "0000" if pin != "0000" else "1111"
    on_shift(dispatch, "akinyi")
    with pytest.raises(Conflict):
        dispatch.deliver("WEB-1", "akinyi", pin)  # not her delivery
    with pytest.raises(WrongPin) as e:
        dispatch.deliver("WEB-1", "otieno", wrong)
    assert e.value.attempts_left == MAX_PIN_ATTEMPTS - 1
    d = dispatch.deliver("WEB-1", "otieno", f" {pin} ")
    assert d["status"] == "delivered" and d["delivered_at"]
    with pytest.raises(Conflict, match="already delivered"):
        dispatch.deliver("WEB-1", "otieno", pin)


def test_too_many_wrong_codes_lock_until_staff_retry(dispatch, inventory):
    pin = out_with_rider(dispatch, inventory)
    wrong = "0000" if pin != "0000" else "1111"
    for _ in range(MAX_PIN_ATTEMPTS - 1):
        with pytest.raises(WrongPin):
            dispatch.deliver("WEB-1", "otieno", wrong)
    with pytest.raises(Locked):
        dispatch.deliver("WEB-1", "otieno", wrong)
    with pytest.raises(Locked):
        dispatch.deliver("WEB-1", "otieno", pin)  # even the right code, once locked
    assert dispatch.get("WEB-1")["locked"]
    dispatch.retry("WEB-1", "mary")
    assert dispatch.deliver("WEB-1", "otieno", pin)["status"] == "delivered"


def test_failed_delivery_and_retry(dispatch, inventory):
    pin = out_with_rider(dispatch, inventory)
    with pytest.raises(ValueError):
        dispatch.fail("WEB-1", "otieno", "")
    d = dispatch.fail("WEB-1", "otieno", "Customer not answering")
    assert (d["status"], d["fail_reason"]) == ("failed", "Customer not answering")
    assert [x["sale_id"] for x in dispatch.list(STORE, ["failed"])] == ["WEB-1"]
    assert dispatch.retry("WEB-1", "mary")["status"] == "picked_up"
    dispatch.deliver("WEB-1", "otieno", pin)
    assert [(e["what"], e["who"]) for e in dispatch.events("WEB-1")] == [
        ("created", "Jane"), ("paid", ""), ("accepted", "otieno"), ("picked_up", "mary"), ("failed", "otieno"),
        ("retry", "mary"), ("delivered", "otieno")]


def test_lists_by_rider_and_status(dispatch, inventory):
    on_shift(dispatch, "otieno", "akinyi")
    for i, rider in enumerate(["otieno", "akinyi", None], 1):
        new_delivery(dispatch, inventory, f"WEB-{i}")
        if rider:
            dispatch.claim(f"WEB-{i}", rider)
    assert [d["sale_id"] for d in dispatch.list(STORE, rider_id="otieno")] == ["WEB-1"]
    assert [d["sale_id"] for d in dispatch.list(STORE, ["unassigned"])] == ["WEB-3"]
    with pytest.raises(ValueError):
        dispatch.list(STORE, ["lost"])
