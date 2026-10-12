import pytest

from warehouse.service import Conflict, NotEnoughStock, NotFound, Unavailable, Warehouse, clean_bin

from .conftest import MILK, SUGAR


def put(wh, ean=MILK, qty=10, bin="A-01", scan="s1"):
    return wh.receive(ean, qty, bin, "DN-1", scan, "wanjiru")


# ------------------------------------------------------------------ products and bins

def test_bin_codes_are_tidied_and_checked():
    assert clean_bin(" a-01-3 ") == "A-01-3"
    for bad in ["", "-A1", "A 01", "A" * 21, None]:
        with pytest.raises(ValueError):
            clean_bin(bad)


def test_products_come_from_inventory_and_are_kept(wh, inventory):
    assert wh.product(MILK)["name"] == "Milk 500ml"
    inventory.products[MILK]["name"] = "Milk 500ml (new pack)"
    assert wh.product(MILK)["name"] == "Milk 500ml (new pack)"  # the copy follows renames
    inventory.down = True
    assert wh.product(MILK)["name"] == "Milk 500ml (new pack)"  # works from the copy while Inventory is down
    with pytest.raises(Unavailable):
        wh.product(SUGAR)  # never seen, and Inventory is down


def test_unknown_and_malformed_barcodes(wh):
    with pytest.raises(NotFound, match="Register it"):
        wh.product("1234567890123")
    with pytest.raises(ValueError):
        wh.product("123")


# ------------------------------------------------------------------ goods in

def test_receive_adds_to_the_bin_once_per_scan(wh):
    r = put(wh)
    assert (r["bin"], r["created"], r["bin_qty"]) == ("A-01", True, 10)
    again = put(wh)  # the phone re-sent the same scan
    assert (again["created"], again["bin_qty"]) == (False, 10)
    assert put(wh, scan="s2", qty=5)["bin_qty"] == 15
    [entry, _] = wh.history()[::-1]
    assert (entry["entry_type"], entry["ref"], entry["recorded_by"]) == ("receive", "DN-1", "wanjiru")


def test_receive_needs_a_quantity(wh):
    with pytest.raises(ValueError):
        put(wh, qty=0)


# ------------------------------------------------------------------ counts

def test_count_sets_the_bin_and_keeps_the_difference(wh):
    put(wh)
    r = wh.count(MILK, "a-01", 7, "c1", "otieno")
    assert (r["was"], r["counted"], r["difference"], r["counted_by"]) == (10, 7, -3, "otieno")
    assert wh.bin_qty("A-01", "milk-500ml") == 7
    assert wh.count(MILK, "A-01", 7, "c1", "otieno")["difference"] == -3  # re-sent: same answer, no change
    assert wh.bin_qty("A-01", "milk-500ml") == 7
    with pytest.raises(Conflict):
        wh.count(MILK, "A-01", 8, "c1")
    [c] = wh.counts()
    assert (c["name"], c["was_qty"], c["counted_qty"]) == ("Milk 500ml", 10, 7)


def test_a_matching_count_writes_no_ledger_entry(wh):
    put(wh)
    assert wh.count(MILK, "A-01", 10, "c1")["difference"] == 0
    assert [e["entry_type"] for e in wh.history()] == ["receive"]


def test_count_of_an_empty_bin_finds_stock(wh):
    assert wh.count(SUGAR, "B-02", 4, "c1")["difference"] == 4
    with pytest.raises(ValueError):
        wh.count(SUGAR, "B-02", -1, "c2")


# ------------------------------------------------------------------ moves

def test_move_between_bins(wh):
    put(wh)
    r = wh.move(MILK, 4, "A-01", "b-07", "m1", "otieno")
    assert (r["from_qty"], r["to_qty"]) == (6, 4)
    wh.move(MILK, 4, "A-01", "B-07", "m1", "otieno")  # re-sent
    assert (wh.bin_qty("A-01", "milk-500ml"), wh.bin_qty("B-07", "milk-500ml")) == (6, 4)


def test_move_refuses_more_than_the_bin_holds(wh):
    put(wh, qty=3)
    with pytest.raises(NotEnoughStock) as e:
        wh.move(MILK, 4, "A-01", "B-07", "m1")
    assert e.value.short == [{"product_id": "milk-500ml", "bin": "A-01", "requested": 4, "available": 3}]
    assert wh.bin_qty("A-01", "milk-500ml") == 3


@pytest.mark.parametrize("qty,to_bin", [(0, "B-07"), (1, "a-01")])
def test_move_needs_a_quantity_and_two_bins(wh, qty, to_bin):
    put(wh)
    with pytest.raises(ValueError):
        wh.move(MILK, qty, "A-01", to_bin, "m1")


# ------------------------------------------------------------------ transfers to stores

def test_send_to_a_store_then_receive_there(wh, inventory):
    put(wh, qty=10, bin="A-01")
    put(wh, qty=5, bin="A-02", scan="s2")
    put(wh, ean=SUGAR, qty=8, bin="C-01", scan="s3")
    t = wh.send("T-0001", "001", [(MILK, "A-01", 6), (MILK, "A-02", 5), (SUGAR, "C-01", 2), (SUGAR, "c-01", 1)], "otieno")
    assert t["status"] == "dispatched"
    assert [(l["bin"], l["product_id"], l["qty"]) for l in t["lines"]] == \
        [("A-01", "milk-500ml", 6), ("A-02", "milk-500ml", 5), ("C-01", "sugar-1kg", 3)]  # same bin merged
    assert {(r["bin"], r["qty"]) for r in wh.stock()} == {("A-01", 4), ("C-01", 5)}  # A-02 emptied: left out
    assert inventory.restocks == {}  # not in the store until it arrives

    r = wh.receive_at_store("T-0001", "mary")
    assert (r["status"], r["received_by"]) == ("received", "mary")
    assert inventory.store_stock("001", MILK) == 11 and inventory.store_stock("001", SUGAR) == 3
    assert inventory.restocks["transfer-T-0001-milk-500ml"] == ("001", MILK, 11, "mary")
    wh.receive_at_store("T-0001", "mary")  # again: nothing more
    assert len(inventory.restocks) == 2


def test_send_is_all_or_nothing(wh):
    put(wh, qty=2)
    with pytest.raises(NotEnoughStock) as e:
        wh.send("T-0001", "001", [(MILK, "A-01", 1), (SUGAR, "C-01", 1)])
    assert e.value.short == [{"product_id": "sugar-1kg", "bin": "C-01", "requested": 1, "available": 0}]
    assert wh.bin_qty("A-01", "milk-500ml") == 2
    with pytest.raises(NotFound):
        wh.transfer("T-0001")


def test_resending_a_transfer(wh):
    put(wh, qty=5)
    wh.send("T-0001", "001", [(MILK, "A-01", 2)])
    assert wh.send("T-0001", "001", [(MILK, "A-01", 2)])["status"] == "dispatched"  # re-sent: once
    assert wh.bin_qty("A-01", "milk-500ml") == 3
    with pytest.raises(Conflict, match="different contents"):
        wh.send("T-0001", "001", [(MILK, "A-01", 3)])
    with pytest.raises(Conflict):
        wh.send("T-0001", "002", [(MILK, "A-01", 2)])


@pytest.mark.parametrize("store,lines", [("001", []), (" ", [(MILK, "A-01", 1)]), ("001", [(MILK, "A-01", 0)])])
def test_send_needs_a_store_and_items(wh, store, lines):
    put(wh)
    with pytest.raises(ValueError):
        wh.send("T-0001", store, lines)


def test_receiving_survives_the_inventory_service_going_down(wh, inventory):
    put(wh)
    put(wh, ean=SUGAR, scan="s2")
    wh.send("T-0001", "001", [(MILK, "A-01", 2), (SUGAR, "A-01", 3)])
    inventory.fail_after = 1  # the first product reaches the store, then the connection drops
    with pytest.raises(Unavailable):
        wh.receive_at_store("T-0001", "mary")
    assert wh.transfer("T-0001")["status"] == "dispatched"
    inventory.fail_after = None
    wh.receive_at_store("T-0001", "mary")  # try again: finishes, nothing counted twice
    assert (inventory.store_stock("001", MILK), inventory.store_stock("001", SUGAR)) == (2, 3)


def test_a_refused_restock_leaves_the_transfer_open(wh, inventory):
    put(wh)
    wh.send("T-0001", "001", [(MILK, "A-01", 2)])
    inventory.refuse = True
    with pytest.raises(Conflict):
        wh.receive_at_store("T-0001")
    assert wh.transfer("T-0001")["status"] == "dispatched"


def test_transfer_lists(wh):
    put(wh)
    wh.send("T-0001", "001", [(MILK, "A-01", 1)])
    wh.send("T-0002", "001", [(MILK, "A-01", 1)])
    wh.receive_at_store("T-0001")
    assert [t["transfer_id"] for t in wh.transfers("dispatched")] == ["T-0002"]
    assert {t["transfer_id"] for t in wh.transfers()} == {"T-0001", "T-0002"}
    assert wh.transfers(limit=1)[0]["lines"][0]["name"] == "Milk 500ml"
    with pytest.raises(ValueError):
        wh.transfers("lost")


# ------------------------------------------------------------------ views

def test_stock_by_bin_and_product(wh):
    put(wh, qty=3, bin="A-01")
    put(wh, ean=SUGAR, qty=2, bin="A-01", scan="s2")
    put(wh, qty=1, bin="B-01", scan="s3")
    assert [(r["bin"], r["name"], r["qty"]) for r in wh.stock()] == \
        [("A-01", "Milk 500ml", 3), ("A-01", "Sugar 1kg", 2), ("B-01", "Milk 500ml", 1)]
    assert [r["product_id"] for r in wh.stock(bin="a-01")] == ["milk-500ml", "sugar-1kg"]
    assert [r["bin"] for r in wh.stock(ean13=MILK)] == ["A-01", "B-01"]
    assert len(wh.history(limit=2)) == 2


def test_warehouses_are_kept_apart(db, inventory, wh):
    put(wh)
    other = Warehouse(db, inventory, "WH2")
    assert other.stock() == [] and other.bin_qty("A-01", "milk-500ml") == 0
