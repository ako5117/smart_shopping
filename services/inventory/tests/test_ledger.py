import pytest

from inventory.service import Conflict, Item, NotFound
from tests.conftest import EAN, FLOUR, MILK, STORE, SUGAR


def test_restock_by_barcode_and_stock_is_sum_of_ledger(inv):
    inv.restock(STORE, EAN[MILK], 24, "scan-1")
    inv.restock(STORE, EAN[MILK], 12, "scan-2")
    assert inv.stock(STORE, MILK) == 36
    assert inv.stock_levels(STORE) == {MILK: 36}


def test_resent_restock_scan_is_not_counted_twice(inv):
    inv.restock(STORE, EAN[MILK], 24, "scan-1")
    again = inv.restock(STORE, EAN[MILK], 24, "scan-1")
    assert not again["created"] and inv.stock(STORE, MILK) == 24


def test_unknown_barcode_is_rejected(inv):
    with pytest.raises(NotFound):
        inv.restock(STORE, "0000000000000", 1, "scan-x")


def test_sale_does_not_touch_stock_until_paid(inv):
    inv.restock(STORE, EAN[SUGAR], 10, "s1")
    inv.create_sale("SALE-1", STORE, [Item(SUGAR, 2)])
    assert inv.stock(STORE, SUGAR) == 10
    inv.commit_sale("SALE-1", "NLJ7RT61SV")
    assert inv.stock(STORE, SUGAR) == 8


def test_commit_is_idempotent(inv):
    inv.restock(STORE, EAN[SUGAR], 10, "s1")
    inv.create_sale("SALE-1", STORE, [Item(SUGAR, 2), Item(FLOUR, 1)])
    for _ in range(3):
        sale = inv.commit_sale("SALE-1", "NLJ7RT61SV")
    assert sale["status"] == "paid" and inv.stock(STORE, SUGAR) == 8 and inv.stock(STORE, FLOUR) == -1


def test_a_second_payment_for_a_paid_sale_is_refused(inv):
    """The customer paid twice (a second payment started while the first was going through): flag it for a refund."""
    inv.restock(STORE, EAN[SUGAR], 10, "s1")
    inv.create_sale("SALE-1", STORE, [Item(SUGAR, 2)])
    inv.commit_sale("SALE-1", "NLJ7RT61SV")
    with pytest.raises(Conflict, match="PAID TWICE.*NLJ7RT61SV.*CARD-123.*refund"):
        inv.commit_sale("SALE-1", "CARD-123")
    assert inv.sale("SALE-1")["payment_ref"] == "NLJ7RT61SV" and inv.stock(STORE, SUGAR) == 8


def test_second_payment_committing_at_the_same_moment_is_refused(inv):
    """Both payments read the sale as unpaid before either committed it."""
    inv.restock(STORE, EAN[SUGAR], 10, "s1")
    inv.create_sale("SALE-1", STORE, [Item(SUGAR, 2)])
    unpaid = inv.db.one("SELECT * FROM sales WHERE sale_id = ?", ("SALE-1",))
    inv.commit_sale("SALE-1", "NLJ7RT61SV")
    real_one = inv.db.one
    calls = []

    def stale_first_read(sql, args=()):
        calls.append(sql)
        return dict(unpaid) if len(calls) == 1 else real_one(sql, args)

    inv.db.one = stale_first_read
    try:
        with pytest.raises(Conflict, match="PAID TWICE"):
            inv.commit_sale("SALE-1", "CARD-123")
    finally:
        inv.db.one = real_one
    assert inv.sale("SALE-1")["payment_ref"] == "NLJ7RT61SV" and inv.stock(STORE, SUGAR) == 8


def test_duplicate_items_in_a_sale_are_merged(inv):
    sale = inv.create_sale("SALE-2", STORE, [Item(MILK, 1), Item(MILK, 2)])
    assert sale["items"] == [{"product_id": MILK, "qty": 3, "unit_price_kes": None}]


def test_same_sale_id_with_different_contents_conflicts(inv):
    inv.create_sale("SALE-1", STORE, [Item(MILK, 1)])
    assert inv.create_sale("SALE-1", STORE, [Item(MILK, 1)])["status"] == "pending_payment"  # safe retry
    with pytest.raises(Conflict):
        inv.create_sale("SALE-1", STORE, [Item(MILK, 2)])


def test_sale_with_unknown_product_writes_nothing(inv, db):
    with pytest.raises(NotFound):
        inv.create_sale("SALE-9", STORE, [Item(MILK, 1), Item("ghost", 1)])
    assert db.one("SELECT COUNT(*) AS n FROM sales")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM sale_items")["n"] == 0


def test_cancelled_sale_cannot_be_committed_and_paid_sale_cannot_be_cancelled(inv):
    inv.create_sale("SALE-1", STORE, [Item(MILK, 1)])
    inv.cancel_sale("SALE-1")
    with pytest.raises(Conflict):
        inv.commit_sale("SALE-1", "REF")
    inv.create_sale("SALE-2", STORE, [Item(MILK, 1)])
    inv.commit_sale("SALE-2", "REF")
    with pytest.raises(Conflict):
        inv.cancel_sale("SALE-2")


def test_returns_limited_to_quantity_sold(inv):
    inv.restock(STORE, EAN[MILK], 10, "s1")
    inv.create_sale("SALE-1", STORE, [Item(MILK, 3)])
    inv.commit_sale("SALE-1", "REF")
    inv.return_items("SALE-1", MILK, 2, "R1")
    assert inv.stock(STORE, MILK) == 9
    with pytest.raises(ValueError):
        inv.return_items("SALE-1", MILK, 2, "R2")  # only 1 left to return
    inv.return_items("SALE-1", MILK, 1, "R3")
    assert inv.stock(STORE, MILK) == 10


def test_adjustment_needs_a_reason(inv):
    with pytest.raises(ValueError):
        inv.adjust(STORE, MILK, -1, " ", "a1")
    assert inv.adjust(STORE, MILK, -1, "damaged", "a1")["stock"] == -1


def test_sale_keeps_the_price_it_was_sold_at(inv):
    inv.upsert_product(MILK, EAN[MILK], "Milk 500 ml", price_kes=65)
    inv.create_sale("SALE-P", STORE, [Item(MILK, 2)])
    inv.upsert_product(MILK, EAN[MILK], "Milk 500 ml", price_kes=70)
    assert inv.sale("SALE-P")["items"] == [{"product_id": MILK, "qty": 2, "unit_price_kes": 65}]


def test_concurrent_requests_stay_consistent(inv):
    """Many threads at once (FastAPI runs requests in a thread pool) must not lose or double-count stock."""
    from concurrent.futures import ThreadPoolExecutor
    inv.restock(STORE, EAN[MILK], 100, "base")

    def work(i):
        inv.restock(STORE, EAN[MILK], 1, f"dup-scan-{i % 5}")  # 5 distinct scans, each sent many times
        inv.create_sale(f"T-{i}", STORE, [Item(MILK, 1)])
        inv.commit_sale(f"T-{i}", f"REF-{i}")
        inv.commit_sale(f"T-{i}", f"REF-{i}")  # a repeated commit changes nothing

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(40)))
    assert inv.stock(STORE, MILK) == 100 + 5 - 40
