"""Committed stock. Stock only changes on restock, sale, return or an approved adjustment
(docs/data-model.md, STOCK_LEDGER). Current stock is always the sum of ledger entries,
never a separately edited number."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .db import Database


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class NotEnoughStock(Conflict):
    """An online order asked for more than is available. short: [{product_id, requested, available}]."""

    def __init__(self, short: List[dict]):
        super().__init__("Not enough stock for: " + ", ".join(s["product_id"] for s in short))
        self.short = short


CHANNELS = ("in_store", "online")
FULFILMENTS = ("collect", "delivery")
HOLD_MINUTES = 15  # an unpaid order holds its items this long; after that they count as available again


@dataclass(frozen=True)
class Item:
    product_id: str
    qty: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Inventory:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------ catalogue

    def upsert_product(self, product_id: str, ean13: str, name: str, price_kes: Optional[int] = None,
                       changed_by: str = "", category: Optional[str] = None) -> None:
        """Add or update a product. Leaving price_kes or category out keeps the current one. Price changes are logged."""
        if price_kes is not None and price_kes <= 0:
            raise ValueError("Price must be at least 1 KES")
        clash = self.db.one("SELECT product_id FROM products WHERE ean13 = ? AND product_id != ?", (ean13, product_id))
        if clash:
            raise Conflict(f"Barcode {ean13} already belongs to {clash['product_id']}")
        old = self.db.one("SELECT price_kes FROM products WHERE product_id = ?", (product_id,))
        with self.db.tx() as c:
            c.execute("INSERT INTO products (product_id, ean13, name, price_kes, category) VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT(product_id) DO UPDATE SET ean13 = excluded.ean13, name = excluded.name, "
                      "price_kes = COALESCE(excluded.price_kes, products.price_kes), "
                      "category = CASE WHEN ? THEN excluded.category ELSE products.category END",
                      (product_id, ean13, name, price_kes, (category or "").strip(), category is not None))
            old_price = old["price_kes"] if old else None
            if price_kes is not None and price_kes != old_price:
                c.execute("INSERT INTO price_changes (product_id, old_price_kes, new_price_kes, changed_by, changed_at) "
                          "VALUES (?, ?, ?, ?, ?)", (product_id, old_price, price_kes, changed_by, now_iso()))

    def products(self) -> List[dict]:
        """Every product, with who last changed its price and when."""
        return self.db.all(
            "SELECT p.*, pc.changed_by AS price_changed_by, pc.changed_at AS price_changed_at FROM products p "
            "LEFT JOIN price_changes pc ON pc.change_id = "
            "(SELECT MAX(change_id) FROM price_changes WHERE product_id = p.product_id) ORDER BY p.product_id")

    def price_history(self, product_id: str, limit: int = 50) -> List[dict]:
        return self.db.all("SELECT * FROM price_changes WHERE product_id = ? ORDER BY change_id DESC LIMIT ?",
                           (product_id, limit))

    def product_by_ean(self, ean13: str) -> dict:
        p = self.db.one("SELECT * FROM products WHERE ean13 = ?", (ean13,))
        if not p:
            raise NotFound(f"No product with barcode {ean13}")
        return p

    # --------------------------------------------------------------- ledger

    def _record(self, c, store_id: str, product_id: str, entry_type: str, qty_change: int,
                idempotency_key: str, reason: str = "", source_ref: str = "", recorded_by: str = "") -> Optional[int]:
        """Write one ledger entry and queue it for the retailer's system. Returns None if the key was seen before."""
        row = c.execute(
            "INSERT INTO stock_ledger (store_id, product_id, entry_type, qty_change, reason, source_ref, "
            "idempotency_key, occurred_at, recorded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (idempotency_key) DO NOTHING RETURNING entry_id",
            (store_id, product_id, entry_type, qty_change, reason, source_ref, idempotency_key, now_iso(),
             recorded_by)).fetchone()
        if row is None:
            return None
        c.execute("INSERT INTO outbox (entry_id, status, next_attempt_at) VALUES (?, 'queued', ?)",
                  (row["entry_id"], now_iso()))
        return row["entry_id"]

    def restock(self, store_id: str, ean13: str, qty: int, scan_id: str, recorded_by: str = "") -> dict:
        if qty <= 0:
            raise ValueError("Restock quantity must be positive")
        product = self.product_by_ean(ean13)
        with self.db.tx() as c:
            created = self._record(c, store_id, product["product_id"], "restock", qty, f"restock:{scan_id}",
                                   source_ref=scan_id, recorded_by=recorded_by)
        return {"product_id": product["product_id"], "created": created is not None,
                "stock": self.stock(store_id, product["product_id"])}

    def adjust(self, store_id: str, product_id: str, qty_change: int, reason: str, ref: str,
               recorded_by: str = "") -> dict:
        if not reason.strip():
            raise ValueError("An adjustment needs a reason")
        if qty_change == 0:
            raise ValueError("Adjustment of zero does nothing")
        with self.db.tx() as c:
            created = self._record(c, store_id, product_id, "adjustment", qty_change, f"adjust:{ref}",
                                   reason=reason, source_ref=ref, recorded_by=recorded_by)
        return {"created": created is not None, "stock": self.stock(store_id, product_id)}

    def stock(self, store_id: str, product_id: str) -> int:
        row = self.db.one("SELECT COALESCE(SUM(qty_change), 0) AS qty FROM stock_ledger "
                          "WHERE store_id = ? AND product_id = ?", (store_id, product_id))
        return row["qty"]

    def stock_levels(self, store_id: str) -> Dict[str, int]:
        rows = self.db.all("SELECT product_id, SUM(qty_change) AS qty FROM stock_ledger "
                           "WHERE store_id = ? GROUP BY product_id ORDER BY product_id", (store_id,))
        return {r["product_id"]: r["qty"] for r in rows}

    def availability(self, store_id: str, now: Optional[datetime] = None) -> Dict[str, dict]:
        """Per product: committed stock, and how much of it unpaid orders are holding (for HOLD_MINUTES)."""
        return _availability(self.db.all, store_id, now)

    def history(self, store_id: str, product_id: str, limit: int = 50) -> List[dict]:
        return self.db.all("SELECT * FROM stock_ledger WHERE store_id = ? AND product_id = ? "
                           "ORDER BY entry_id DESC LIMIT ?", (store_id, product_id, limit))

    # ---------------------------------------------------------------- sales

    def create_sale(self, sale_id: str, store_id: str, items: List[Item], channel: str = "in_store",
                    customer_name: str = "", check_stock: bool = False, fulfilment: str = "collect",
                    delivery_fee_kes: int = 0, customer_phone: str = "") -> dict:
        """Called at checkout, before payment. Stock is not touched until the sale is paid.

        With check_stock (online orders, where the customer isn't holding the items), the sale is refused with
        NotEnoughStock unless each item is available: committed stock less what other unpaid orders hold.
        The check and the insert share one transaction, and the database lock lets one run at a time.
        """
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {', '.join(CHANNELS)}")
        if fulfilment not in FULFILMENTS:
            raise ValueError(f"fulfilment must be one of {', '.join(FULFILMENTS)}")
        if fulfilment == "delivery" and channel != "online":
            raise ValueError("Only online orders can be delivered")
        if delivery_fee_kes < 0 or (delivery_fee_kes and fulfilment != "delivery"):
            raise ValueError("A delivery fee only applies to deliveries")
        if not items:
            raise ValueError("A sale needs at least one item")
        merged: Dict[str, int] = {}
        for it in items:
            if it.qty <= 0:
                raise ValueError("Item quantities must be positive")
            merged[it.product_id] = merged.get(it.product_id, 0) + it.qty
        existing = self.db.one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
        if existing:
            if existing["store_id"] != store_id or self._items(sale_id) != merged:
                raise Conflict(f"Sale {sale_id} already exists with different contents")
            return self.sale(sale_id)
        ts = now_iso()
        with self.db.tx() as c:
            if check_stock:
                avail = _availability(lambda sql, a: c.execute(sql, a).fetchall(), store_id)
                short = []
                for pid, qty in merged.items():
                    a = avail.get(pid, {"stock": 0, "held": 0})
                    available = max(0, a["stock"] - a["held"])
                    if qty > available:
                        short.append({"product_id": pid, "requested": qty, "available": available})
                if short:
                    raise NotEnoughStock(short)
            c.execute("INSERT INTO sales (sale_id, store_id, status, created_at, updated_at, channel, customer_name, "
                      "fulfilment, delivery_fee_kes, customer_phone) VALUES (?, ?, 'pending_payment', ?, ?, ?, ?, ?, ?, ?)",
                      (sale_id, store_id, ts, ts, channel, customer_name.strip(), fulfilment, delivery_fee_kes,
                       customer_phone.strip()))
            for pid, qty in merged.items():
                product = c.execute("SELECT price_kes FROM products WHERE product_id = ?", (pid,)).fetchone()
                if not product:
                    raise NotFound(f"Unknown product {pid}")
                # The price at the time of sale, so later price changes don't alter receipts.
                c.execute("INSERT INTO sale_items (sale_id, product_id, qty, unit_price_kes) VALUES (?, ?, ?, ?)",
                          (sale_id, pid, qty, product["price_kes"]))
        return self.sale(sale_id)

    def commit_sale(self, sale_id: str, payment_ref: str) -> dict:
        """Called when payment is confirmed. Idempotent: committing twice changes nothing."""
        sale = self.db.one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
        if not sale:
            raise NotFound(f"No sale {sale_id}")
        if sale["status"] == "paid":
            return self.sale(sale_id)
        if sale["status"] == "cancelled":
            raise Conflict(f"Sale {sale_id} was cancelled; a payment arrived for it and needs a manual refund check")
        with self.db.tx() as c:
            for it in self._items(sale_id).items():
                self._record(c, sale["store_id"], it[0], "sale", -it[1], f"sale:{sale_id}:{it[0]}", source_ref=sale_id)
            c.execute("UPDATE sales SET status = 'paid', payment_ref = ?, updated_at = ? WHERE sale_id = ?",
                      (payment_ref, now_iso(), sale_id))
        return self.sale(sale_id)

    def cancel_sale(self, sale_id: str) -> dict:
        sale = self.db.one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
        if not sale:
            raise NotFound(f"No sale {sale_id}")
        if sale["status"] == "paid":
            raise Conflict("A paid sale cannot be cancelled; record a return instead")
        with self.db.tx() as c:
            c.execute("UPDATE sales SET status = 'cancelled', updated_at = ? WHERE sale_id = ?", (now_iso(), sale_id))
        return self.sale(sale_id)

    def return_items(self, sale_id: str, product_id: str, qty: int, return_id: str) -> dict:
        sale = self.db.one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
        if not sale or sale["status"] != "paid":
            raise Conflict("Only items from a paid sale can be returned")
        sold = self._items(sale_id).get(product_id, 0)
        returned = self.db.one("SELECT COALESCE(SUM(qty_change), 0) AS q FROM stock_ledger WHERE entry_type = 'return' "
                                "AND source_ref = ? AND product_id = ?", (sale_id, product_id))["q"]
        if qty <= 0 or qty + returned > sold:
            raise ValueError(f"Can return at most {sold - returned} of {product_id} from sale {sale_id}")
        with self.db.tx() as c:
            self._record(c, sale["store_id"], product_id, "return", qty, f"return:{return_id}", source_ref=sale_id)
        return {"stock": self.stock(sale["store_id"], product_id)}

    def sale(self, sale_id: str) -> dict:
        sale = self.db.one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
        if not sale:
            raise NotFound(f"No sale {sale_id}")
        sale["items"] = self.db.all("SELECT product_id, qty, unit_price_kes FROM sale_items WHERE sale_id = ? "
                                    "ORDER BY product_id", (sale_id,))
        return sale

    def find_sale_by_code(self, store_id: str, code: str) -> dict:
        """Find a sale from the short code on a Scan & Go pass (the end of its sale_id), or its full id."""
        code = code.strip().upper()
        if len(code) < 6 or not re.fullmatch(r"[A-Z0-9-]+", code):
            raise ValueError("Enter at least 6 letters or digits of the order code")
        rows = self.db.all("SELECT sale_id FROM sales WHERE store_id = ? AND (sale_id = ? OR sale_id LIKE ?) "
                           "ORDER BY created_at DESC LIMIT 2", (store_id, code, "%" + code))
        if not rows:
            raise NotFound(f"No order {code} in this store")
        if len(rows) > 1 and rows[0]["sale_id"] != code:
            raise Conflict(f"More than one order ends in {code}; enter the full order number")
        return self.sale(rows[0]["sale_id"])

    def record_exit(self, sale_id: str, checked_by: str) -> dict:
        """Staff checked the customer's pass at the exit. A pass works once."""
        sale = self.db.one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
        if not sale:
            raise NotFound(f"No sale {sale_id}")
        if sale["status"] != "paid":
            raise Conflict(f"Order {sale_id} is not paid")
        if sale["exited_at"]:
            raise Conflict(f"Order {sale_id} already left the store at {sale['exited_at']} (checked by {sale['exited_by']})")
        with self.db.tx() as c:
            cur = c.execute("UPDATE sales SET exited_at = ?, exited_by = ? WHERE sale_id = ? AND exited_at IS NULL",
                            (now_iso(), checked_by, sale_id))
            if cur.rowcount == 0:
                raise Conflict(f"Order {sale_id} already left the store")
        return self.sale(sale_id)

    def mark_ready(self, sale_id: str, ready_by: str) -> dict:
        """An online order is packed and waiting at the store for the customer. Marking it twice changes nothing."""
        sale = self.db.one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
        if not sale:
            raise NotFound(f"No sale {sale_id}")
        if sale["channel"] != "online":
            raise Conflict(f"Order {sale_id} isn't an online order")
        if sale["status"] != "paid":
            raise Conflict(f"Order {sale_id} is not paid")
        if sale["exited_at"]:
            raise Conflict(f"Order {sale_id} was already collected")
        if not sale["ready_at"]:
            with self.db.tx() as c:
                c.execute("UPDATE sales SET ready_at = ?, ready_by = ? WHERE sale_id = ? AND ready_at IS NULL",
                          (now_iso(), ready_by, sale_id))
        return self.sale(sale_id)

    def recent_sales(self, store_id: str, limit: int = 50, channel: Optional[str] = None,
                     status: Optional[str] = None, uncollected: bool = False) -> List[dict]:
        filters = {"store_id = ?": store_id, "channel = ?": channel, "status = ?": status}
        where = [cond for cond, value in filters.items() if value]
        args = [value for value in filters.values() if value]
        if uncollected:
            where.append("exited_at IS NULL")
        sales = self.db.all(f"SELECT * FROM sales WHERE {' AND '.join(where)} "
                            "ORDER BY created_at DESC, sale_id DESC LIMIT ?", (*args, limit))
        for s in sales:
            s["items"] = self.db.all("SELECT product_id, qty, unit_price_kes FROM sale_items WHERE sale_id = ? "
                                     "ORDER BY product_id", (s["sale_id"],))
        return sales

    def _items(self, sale_id: str) -> Dict[str, int]:
        return {r["product_id"]: r["qty"] for r in
                self.db.all("SELECT product_id, qty FROM sale_items WHERE sale_id = ? ORDER BY product_id", (sale_id,))}


def _availability(query, store_id: str, now: Optional[datetime] = None) -> Dict[str, dict]:
    """query(sql, args) -> rows; works with Database.all or inside a transaction."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(minutes=HOLD_MINUTES)).isoformat()
    out: Dict[str, dict] = {}
    for r in query("SELECT product_id, SUM(qty_change) AS qty FROM stock_ledger WHERE store_id = ? "
                   "GROUP BY product_id", (store_id,)):
        out[r["product_id"]] = {"stock": r["qty"], "held": 0}
    for r in query("SELECT si.product_id, SUM(si.qty) AS qty FROM sale_items si JOIN sales s ON s.sale_id = si.sale_id "
                   "WHERE s.store_id = ? AND s.status = 'pending_payment' AND s.created_at >= ? "
                   "GROUP BY si.product_id", (store_id, cutoff)):
        out.setdefault(r["product_id"], {"stock": 0, "held": 0})["held"] = r["qty"]
    return out
