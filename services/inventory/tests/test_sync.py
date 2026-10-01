from datetime import datetime, timedelta, timezone

from inventory import sync
from inventory.service import Item
from tests.conftest import EAN, MILK, STORE, SUGAR

LATER = datetime.now(timezone.utc) + timedelta(days=1)


def test_outbox_sends_every_change_once_in_order(inv, db, retailer):
    inv.restock(STORE, EAN[MILK], 10, "s1")
    inv.create_sale("SALE-1", STORE, [Item(MILK, 2)])
    inv.commit_sale("SALE-1", "REF")
    assert sync.drain_outbox(db, retailer) == {"sent": 2, "pending": 0}
    assert [m["qty_change"] for m in retailer.received] == [10, -2]
    assert sync.drain_outbox(db, retailer)["sent"] == 0


def test_failure_stops_the_queue_and_retries_later(inv, db, retailer):
    inv.restock(STORE, EAN[MILK], 10, "s1")
    inv.restock(STORE, EAN[SUGAR], 5, "s2")
    retailer.fail_next = 1
    assert sync.drain_outbox(db, retailer) == {"sent": 0, "pending": 2}
    assert sync.drain_outbox(db, retailer)["sent"] == 0  # not due yet (30 s backoff)
    assert sync.drain_outbox(db, retailer, now=LATER) == {"sent": 2, "pending": 0}
    assert [m["product_id"] for m in retailer.received] == [MILK, SUGAR]  # order kept


def test_backoff_grows_then_goes_hourly():
    assert [sync._delay_after(n) for n in (1, 2, 3, 4, 9)] == [30, 120, 600, 3600, 3600]


def test_gap_explained_by_unsent_changes_is_timing_and_self_resolves(inv, db, retailer):
    inv.restock(STORE, EAN[MILK], 10, "s1")
    [d] = sync.reconcile(db, STORE, {MILK: 0})
    assert d["category"] == "timing"
    sync.drain_outbox(db, retailer)
    assert sync.reconcile(db, STORE, {MILK: 10}) == []
    assert sync.open_discrepancies(db, STORE) == []


def test_unexplained_gap_is_unknown_and_resolved_by_a_count(inv, db, retailer):
    inv.restock(STORE, EAN[MILK], 30, "s1")
    sync.drain_outbox(db, retailer)
    [d] = sync.reconcile(db, STORE, {MILK: 28})
    assert d["category"] == "unknown"
    [open_d] = sync.open_discrepancies(db, STORE)
    result = sync.resolve_with_count(db, inv, open_d["discrepancy_id"], counted_qty=28, counted_by="Mary")
    assert result == {"adjusted_by": -2, "stock": 28}
    assert sync.open_discrepancies(db, STORE) == []
    assert inv.history(STORE, MILK)[0]["reason"] == "stock count by Mary"


def test_tolerance_ignores_small_gaps(inv, db, retailer):
    inv.restock(STORE, EAN[MILK], 30, "s1")
    sync.drain_outbox(db, retailer)
    assert sync.reconcile(db, STORE, {MILK: 29}, tolerance=1) == []


def test_product_only_the_retailer_has_is_flagged(inv, db):
    [d] = sync.reconcile(db, STORE, {SUGAR: 4})
    assert (d["product_id"], d["our_qty"], d["retailer_qty"], d["category"]) == (SUGAR, 0, 4, "unknown")
