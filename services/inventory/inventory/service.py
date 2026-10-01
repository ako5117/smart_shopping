"""Committed stock. Stock only changes on restock, sale, return or an approved adjustment
(docs/data-model.md, STOCK_LEDGER). Current stock is always the sum of ledger entries,
never a separately edited number."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .db import Database


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


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

    def upsert_product(self, product_id: str, ean13: str, name: str) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO products (product_id, ean13, name) VALUES (?, ?, ?) "
                      "ON CONFLICT(product_id) DO UPDATE SET ean13 = excluded.ean13, name = excluded.name",
                      (product_id, ean13, name))

    def product_by_ean(self, ean13: str) -> dict:
        p = self.db.one("SELECT * FROM products WHERE ean13 = ?", (ean13,))
        if not p:
            raise NotFound(f"No product with barcode {ean13}")
        return p

    # --------------------------------------------------------------- ledger

    def _record(self, c, store_id: str, product_id: str, entry_type: str, qty_change: int,
                idempotency_key: str, reason: str = "", source_ref: str = "") -> Optional[int]:
        """Write one ledger entry and queue it for the retailer's system. Returns None if the key was seen before."""
        cur = c.execute(
            "INSERT OR IGNORE INTO stock_ledger (store_id, product_id, entry_type, qty_change, reason, source_ref, "
            "idempotency_key, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (store_id, product_id, entry_type, qty_change, reason, source_ref, idempotency_key, now_iso()))
        if cur.rowcount == 0:
            return None
        c.execute("INSERT INTO outbox (entry_id, status, next_attempt_at) VALUES (?, 'queued', ?)",
                  (cur.lastrowid, now_iso()))
        return cur.lastrowid

    def restock(self, store_id: str, ean13: str, qty: int, scan_id: str) -> dict:
        if qty <= 0:
            raise ValueError("Restock quantity must be positive")
        product = self.product_by_ean(ean13)
        with self.db.tx() as c:
            created = self._record(c, store_id, product["product_id"], "restock", qty, f"restock:{scan_id}",
                                   source_ref=scan_id)
        return {"product_id": product["product_id"], "created": created is not None,
                "stock": self.stock(store_id, product["product_id"])}

    def adjust(self, store_id: str, product_id: str, qty_change: int, reason: str, ref: str) -> dict:
        if not reason.strip():
            raise ValueError("An adjustment needs a reason")
        if qty_change == 0:
            raise ValueError("Adjustment of zero does nothing")
        with self.db.tx() as c:
            created = self._record(c, store_id, product_id, "adjustment", qty_change, f"adjust:{ref}",
                                   reason=reason, source_ref=ref)
        return {"created": created is not None, "stock": self.stock(store_id, product_id)}

    def stock(self, store_id: str, product_id: str) -> int:
        row = self.db.one("SELECT COALESCE(SUM(qty_change), 0) AS qty FROM stock_ledger "
                          "WHERE store_id = ? AND product_id = ?", (store_id, product_id))
        return row["qty"]

    def stock_levels(self, store_id: str) -> Dict[str, int]:
        rows = self.db.all("SELECT product_id, SUM(qty_change) AS qty FROM stock_ledger "
                           "WHERE store_id = ? GROUP BY product_id ORDER BY product_id", (store_id,))
        return {r["product_id"]: r["qty"] for r in rows}

    def history(self, store_id: str, product_id: str, limit: int = 50) -> List[dict]:
        return self.db.all("SELECT * FROM stock_ledger WHERE store_id = ? AND product_id = ? "
                           "ORDER BY entry_id DESC LIMIT ?", (store_id, product_id, limit))

    # ---------------------------------------------------------------- sales

    def create_sale(self, sale_id: str, store_id: str, items: List[Item]) -> dict:
        """Called at checkout, before payment. Stock is not touched until the sale is paid."""
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
            c.execute("INSERT INTO sales (sale_id, store_id, status, created_at, updated_at) "
                      "VALUES (?, ?, 'pending_payment', ?, ?)", (sale_id, store_id, ts, ts))
            for pid, qty in merged.items():
                if not c.execute("SELECT 1 FROM products WHERE product_id = ?", (pid,)).fetchone():
                    raise NotFound(f"Unknown product {pid}")
                c.execute("INSERT INTO sale_items (sale_id, product_id, qty) VALUES (?, ?, ?)", (sale_id, pid, qty))
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
        sale["items"] = [{"product_id": p, "qty": q} for p, q in self._items(sale_id).items()]
        return sale

    def _items(self, sale_id: str) -> Dict[str, int]:
        return {r["product_id"]: r["qty"] for r in
                self.db.all("SELECT product_id, qty FROM sale_items WHERE sale_id = ? ORDER BY product_id", (sale_id,))}
