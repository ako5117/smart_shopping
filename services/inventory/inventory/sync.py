"""Sending committed stock changes to the retailer's system, and reconciling counts
(docs/reconciliation.md)."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Protocol

import httpx

from .db import Database
from .service import now_iso

log = logging.getLogger("inventory.sync")

RETRY_DELAYS_S = [30, 120, 600]  # then hourly
HOURLY_S = 3600


class RetailerAdapter(Protocol):
    def send(self, movement: dict) -> None:
        """Deliver one stock movement. Must be idempotent on movement['idempotency_key']. Raise on failure."""

    def stock_snapshot(self, store_id: str) -> Dict[str, int]:
        """Current stock per product_id according to the retailer's system."""


class HttpRetailerAdapter:
    """Placeholder REST contract until a pilot retailer's actual API is known.

    POST {base}/stock-movements   body: movement, header Idempotency-Key
    GET  {base}/stock?store_id=   -> {"product_id": qty, ...}
    """

    def __init__(self, base_url: str, api_key: str = "", http: Optional[httpx.Client] = None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = http or httpx.Client(base_url=base_url, timeout=15.0, headers=headers)

    def send(self, movement: dict) -> None:
        r = self.http.post("/stock-movements", json=movement, headers={"Idempotency-Key": movement["idempotency_key"]})
        r.raise_for_status()

    def stock_snapshot(self, store_id: str) -> Dict[str, int]:
        r = self.http.get("/stock", params={"store_id": store_id})
        r.raise_for_status()
        return {k: int(v) for k, v in r.json().items()}


def _delay_after(attempts: int) -> int:
    return RETRY_DELAYS_S[attempts - 1] if attempts <= len(RETRY_DELAYS_S) else HOURLY_S


def drain_outbox(db: Database, adapter: RetailerAdapter, now: Optional[datetime] = None, limit: int = 100) -> dict:
    """Send due outbox items in ledger order. Stops at the first failure so the retailer sees changes in order."""
    now = now or datetime.now(timezone.utc)
    due = db.all(
        "SELECT o.outbox_id, o.attempts, o.next_attempt_at, l.* FROM outbox o JOIN stock_ledger l ON l.entry_id = o.entry_id "
        "WHERE o.status = 'queued' ORDER BY o.entry_id LIMIT ?", (limit,))
    sent = 0
    for row in due:
        if datetime.fromisoformat(row["next_attempt_at"]) > now:
            break
        movement = {k: row[k] for k in ("store_id", "product_id", "entry_type", "qty_change", "reason",
                                         "source_ref", "idempotency_key", "occurred_at")}
        try:
            adapter.send(movement)
        except Exception as e:  # network, HTTP error, anything: retry later
            attempts = row["attempts"] + 1
            retry_at = now + timedelta(seconds=_delay_after(attempts))
            with db.tx() as c:
                c.execute("UPDATE outbox SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE outbox_id = ?",
                          (attempts, retry_at.isoformat(), str(e)[:300], row["outbox_id"]))
            log.warning("Retailer sync failed (attempt %d), retry at %s: %s", attempts, retry_at, e)
            break
        with db.tx() as c:
            c.execute("UPDATE outbox SET status = 'confirmed', attempts = attempts + 1 WHERE outbox_id = ?",
                      (row["outbox_id"],))
        sent += 1
    pending = db.one("SELECT COUNT(*) AS n FROM outbox WHERE status = 'queued'")["n"]
    return {"sent": sent, "pending": pending}


def _unconfirmed_change(db: Database, store_id: str, product_id: str) -> int:
    return db.one(
        "SELECT COALESCE(SUM(l.qty_change), 0) AS q FROM outbox o JOIN stock_ledger l ON l.entry_id = o.entry_id "
        "WHERE o.status = 'queued' AND l.store_id = ? AND l.product_id = ?", (store_id, product_id))["q"]


def reconcile(db: Database, store_id: str, retailer_stock: Dict[str, int], tolerance: int = 0) -> List[dict]:
    """Compare our stock with the retailer's snapshot and record differences.

    timing  - our unsent changes exactly explain the gap; resolves itself once the outbox drains
    unknown - needs a staff count; resolved by recording an adjustment
    Open timing discrepancies that now match are resolved automatically.
    """
    ours = {r["product_id"]: r["qty"] for r in db.all(
        "SELECT product_id, SUM(qty_change) AS qty FROM stock_ledger WHERE store_id = ? GROUP BY product_id",
        (store_id,))}
    found = []
    with db.tx() as c:
        for pid in sorted(set(ours) | set(retailer_stock)):
            our_qty, their_qty = ours.get(pid, 0), retailer_stock.get(pid, 0)
            gap = our_qty - their_qty
            open_rows = c.execute("SELECT discrepancy_id FROM discrepancies WHERE store_id = ? AND product_id = ? "
                                  "AND status = 'open'", (store_id, pid)).fetchall()
            if abs(gap) <= tolerance:
                for r in open_rows:
                    c.execute("UPDATE discrepancies SET status = 'resolved', resolution = 'counts now agree', "
                              "resolved_at = ? WHERE discrepancy_id = ?", (now_iso(), r["discrepancy_id"]))
                continue
            category = "timing" if gap == _unconfirmed_change(db, store_id, pid) else "unknown"
            if open_rows:
                c.execute("UPDATE discrepancies SET our_qty = ?, retailer_qty = ?, category = ? WHERE discrepancy_id = ?",
                          (our_qty, their_qty, category, open_rows[0]["discrepancy_id"]))
            else:
                c.execute("INSERT INTO discrepancies (store_id, product_id, our_qty, retailer_qty, category, status, "
                          "created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
                          (store_id, pid, our_qty, their_qty, category, now_iso()))
            found.append({"product_id": pid, "our_qty": our_qty, "retailer_qty": their_qty, "category": category})
    return found


def open_discrepancies(db: Database, store_id: str) -> List[dict]:
    return db.all("SELECT * FROM discrepancies WHERE store_id = ? AND status = 'open' ORDER BY product_id", (store_id,))


def resolve_with_count(db: Database, inventory, discrepancy_id: int, counted_qty: int, counted_by: str) -> dict:
    """A staff member counted the shelf. Record the difference as an adjustment and close the discrepancy."""
    d = db.one("SELECT * FROM discrepancies WHERE discrepancy_id = ?", (discrepancy_id,))
    if not d or d["status"] != "open":
        raise ValueError("No open discrepancy with that id")
    change = counted_qty - inventory.stock(d["store_id"], d["product_id"])
    if change:
        inventory.adjust(d["store_id"], d["product_id"], change, f"stock count by {counted_by}",
                         f"count:{discrepancy_id}")
    with db.tx() as c:
        c.execute("UPDATE discrepancies SET status = 'resolved', resolution = ?, resolved_at = ? WHERE discrepancy_id = ?",
                  (f"counted {counted_qty} by {counted_by}", now_iso(), discrepancy_id))
    return {"adjusted_by": change, "stock": inventory.stock(d["store_id"], d["product_id"])}
