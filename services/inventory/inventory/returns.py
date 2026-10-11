"""Returns and refunds.

The store's rules:
    - only items that aren't perishable (products marked perishable, such as milk and bread, are never taken back)
    - unopened, in the same state as when sold
    - within RETURN_DAYS of payment (14 by default)

How a return goes:
    1. requested   the customer asks online (from their order page), or staff start it at the counter.
                   Either way the customer confirms the items are unopened and as sold.
    2. approved    a manager checks the items and approves: they go back into stock (a "return" entry in the
                   stock ledger), and the refund is worked out: what was actually paid for those items. A
                   points discount on the order is shared across its items; the delivery fee isn't refunded.
       rejected    or the manager rejects it, saying why (e.g. opened, damaged, past the deadline).
    3. refunded    the money goes back by hand (M-Pesa, card or cash) and the manager records how and the reference.

Quantities can't be returned twice: what's already returned or waiting to be checked comes off what's left.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .service import Conflict, NotFound, now_iso

REFUND_METHODS = ("mpesa", "card", "cash")
OPEN = ("requested", "approved", "refunded")  # these hold their quantities


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None
    return t.replace(tzinfo=timezone.utc) if t and t.tzinfo is None else t


class Returns:
    def __init__(self, inventory, days: int = 14, clock=None):
        if days < 0:
            raise ValueError("RETURN_DAYS can't be negative")
        self.inv, self.db, self.days = inventory, inventory.db, days
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ what can go back

    def _held(self, sale_id: str) -> Dict[str, int]:
        """Per product: already returned (stock ledger) or in a return still being dealt with."""
        held: Dict[str, int] = {}
        for r in self.db.all("SELECT product_id, COALESCE(SUM(qty_change), 0) AS q FROM stock_ledger "
                             "WHERE entry_type = 'return' AND source_ref = ? GROUP BY product_id", (sale_id,)):
            held[r["product_id"]] = r["q"]
        for r in self.db.all("SELECT l.product_id, SUM(l.qty) AS q FROM return_lines l JOIN returns r ON r.return_id = l.return_id "
                             "WHERE r.sale_id = ? AND r.status = 'requested' GROUP BY l.product_id", (sale_id,)):
            held[r["product_id"]] = held.get(r["product_id"], 0) + r["q"]
        return held

    def returnable(self, sale_id: str) -> dict:
        """The sale's items and how many of each can still be returned, with the reason when none can."""
        sale = self.inv.sale(sale_id)
        paid = _parse(sale.get("paid_at")) or _parse(sale.get("created_at"))  # older sales: the conservative date
        deadline = paid + timedelta(days=self.days) if paid else None
        open_ = sale["status"] == "paid" and deadline is not None and self.clock() <= deadline
        products = {p["product_id"]: p for p in self.inv.products()}
        held = self._held(sale_id)
        goods = sum((it.get("unit_price_kes") or 0) * it["qty"] for it in sale["items"])
        share = 1 - (sale.get("loyalty_discount_kes") or 0) / goods if goods else 1
        items = []
        for it in sale["items"]:
            p = products.get(it["product_id"], {})
            left = max(0, it["qty"] - held.get(it["product_id"], 0))
            why = ("Food and other perishables can't be returned" if p.get("perishable")
                   else "Already returned" if not left else "")
            items.append({"product_id": it["product_id"], "name": p.get("name", it["product_id"]), "qty": it["qty"],
                          "returnable": 0 if why or not open_ else left, "perishable": bool(p.get("perishable")),
                          "unit_refund_kes": int((it.get("unit_price_kes") or 0) * share), "why_not": why})
        reason = "" if open_ else ("Only paid orders can be returned" if sale["status"] != "paid"
                                   else f"The {self.days}-day return period ended on {deadline:%d %b %Y}")
        return {"sale_id": sale_id, "status": sale["status"], "days": self.days, "deadline": deadline.isoformat() if deadline else None,
                "open": open_ and any(i["returnable"] for i in items), "reason": reason, "items": items,
                "returns": self.for_sale(sale_id)}

    # ------------------------------------------------------------------ the steps

    def request(self, sale_id: str, lines: List[dict], reason: str, started_by: str, unopened: bool) -> dict:
        """Start a return: lines are [{product_id, qty}]. The customer confirms the items are unopened and as sold."""
        reason = " ".join((reason or "").split())
        if not 3 <= len(reason) <= 300:
            raise ValueError("Say why the items are coming back")
        if not unopened:
            raise ValueError("Items can only be returned unopened and in the same state as when sold")
        wanted: Dict[str, int] = {}
        for l in lines:
            if int(l["qty"]) <= 0:
                raise ValueError("Quantities must be positive")
            wanted[l["product_id"]] = wanted.get(l["product_id"], 0) + int(l["qty"])
        if not wanted:
            raise ValueError("Choose what to return")
        return_id = "RET-" + secrets.token_hex(5).upper()
        with self.db.tx() as c:  # the check and the insert together, so two requests can't both take the last item
            r = self.returnable(sale_id)
            if not r["open"] and r["reason"]:
                raise Conflict(r["reason"])
            by_id = {i["product_id"]: i for i in r["items"]}
            for pid, qty in wanted.items():
                i = by_id.get(pid)
                if not i:
                    raise ValueError(f"{pid} wasn't in this order")
                if i["why_not"]:
                    raise Conflict(f"{i['name']}: {i['why_not']}")
                if qty > i["returnable"]:
                    raise Conflict(f"Only {i['returnable']} of {i['name']} can be returned")
            c.execute("INSERT INTO returns (return_id, sale_id, store_id, status, reason, started_by, created_at) "
                      "VALUES (?, ?, ?, 'requested', ?, ?, ?)",
                      (return_id, sale_id, self.inv.sale(sale_id)["store_id"], reason, started_by or "customer", now_iso()))
            for pid, qty in wanted.items():
                c.execute("INSERT INTO return_lines (return_id, product_id, qty, refund_kes) VALUES (?, ?, ?, ?)",
                          (return_id, pid, qty, by_id[pid]["unit_refund_kes"] * qty))
        return self.get(return_id)

    def approve(self, return_id: str, by: str, checked: bool, note: str = "") -> dict:
        """A manager has checked the items (unopened, as sold): they go back into stock, and the refund is due."""
        if not checked:
            raise ValueError("Check the items first: unopened and in the same state as when sold")
        r = self.get(return_id)
        if r["status"] != "requested":
            raise Conflict(f"This return is already {r['status']}")
        with self.db.tx() as c:
            cur = c.execute("UPDATE returns SET status = 'approved', decided_by = ?, decided_at = ?, note = ?, refund_kes = ? "
                            "WHERE return_id = ? AND status = 'requested'",
                            (by, now_iso(), (note or "").strip(), sum(l["refund_kes"] for l in r["lines"]), return_id))
            if cur.rowcount != 1:
                raise Conflict("Someone else dealt with this return a moment ago")
            for l in r["lines"]:
                self.inv._record(c, r["store_id"], l["product_id"], "return", l["qty"], f"return:{return_id}:{l['product_id']}",
                                 source_ref=r["sale_id"], recorded_by=by)
        return self.get(return_id)

    def reject(self, return_id: str, by: str, note: str) -> dict:
        note = " ".join((note or "").split())
        if len(note) < 3:
            raise ValueError("Say why the return is refused (the customer is told)")
        with self.db.tx() as c:
            cur = c.execute("UPDATE returns SET status = 'rejected', decided_by = ?, decided_at = ?, note = ? "
                            "WHERE return_id = ? AND status = 'requested'", (by, now_iso(), note, return_id))
            if cur.rowcount != 1:
                self.get(return_id)
                raise Conflict("This return has already been dealt with")
        return self.get(return_id)

    def refunded(self, return_id: str, by: str, method: str, ref: str = "") -> dict:
        """The money went back (by hand, for now): record how."""
        if method not in REFUND_METHODS:
            raise ValueError("The refund went back by mpesa, card or cash")
        ref = (ref or "").strip()
        if method != "cash" and not ref:
            raise ValueError("Enter the M-Pesa or card reference of the refund")
        with self.db.tx() as c:
            cur = c.execute("UPDATE returns SET status = 'refunded', refund_method = ?, refund_ref = ?, refunded_by = ?, refunded_at = ? "
                            "WHERE return_id = ? AND status = 'approved'", (method, ref, by, now_iso(), return_id))
            if cur.rowcount != 1:
                r = self.get(return_id)
                raise Conflict("Approve the return before recording the refund" if r["status"] == "requested"
                               else f"This return is {r['status']}")
        return self.get(return_id)

    # ------------------------------------------------------------------ reading

    def get(self, return_id: str) -> dict:
        r = self.db.one("SELECT * FROM returns WHERE return_id = ?", (return_id,))
        if not r:
            raise NotFound(f"No return {return_id}")
        return self._with_lines(r)

    def _with_lines(self, r: dict) -> dict:
        r["lines"] = self.db.all("SELECT l.product_id, p.name, l.qty, l.refund_kes FROM return_lines l "
                                 "LEFT JOIN products p ON p.product_id = l.product_id WHERE l.return_id = ? ORDER BY l.product_id",
                                 (r["return_id"],))
        r["total_kes"] = sum(l["refund_kes"] for l in r["lines"])
        return r

    def for_sale(self, sale_id: str) -> List[dict]:
        return [self._with_lines(r) for r in self.db.all("SELECT * FROM returns WHERE sale_id = ? ORDER BY created_at", (sale_id,))]

    def list(self, store_id: str, statuses: Optional[List[str]] = None, limit: int = 100) -> List[dict]:
        statuses = statuses or ["requested", "approved"]
        if any(s not in ("requested", "approved", "rejected", "refunded") for s in statuses):
            raise ValueError("Unknown return status")
        marks = ",".join("?" * len(statuses))
        rows = self.db.all(f"SELECT r.*, s.channel, s.customer_name, s.customer_phone FROM returns r JOIN sales s ON s.sale_id = r.sale_id "
                           f"WHERE r.store_id = ? AND r.status IN ({marks}) ORDER BY r.created_at LIMIT ?",
                           (store_id, *statuses, limit))
        return [self._with_lines(r) for r in rows]
