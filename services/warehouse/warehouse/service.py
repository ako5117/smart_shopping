"""Warehouse (go-down) stock, per bin. Like the stores' stock (services/inventory), it is a ledger: stock in a bin
only changes when goods are received, moved between bins, sent to a store, or counted, and is always the sum of
those entries. Every write carries an id made once by the phone, so a re-sent scan is counted once.

Products and barcodes belong to the Inventory Service; the warehouse keeps a copy of each product's name and barcode
the first time it's scanned, so stock lists still read well if the Inventory Service is briefly down.
"""

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .db import Database

EAN13 = re.compile(r"^\d{13}$")
BIN = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,19}$")


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Unavailable(Exception):
    """The Inventory Service couldn't be reached."""


class NotEnoughStock(Conflict):
    """short: [{product_id, bin, requested, available}]"""

    def __init__(self, short: List[dict]):
        super().__init__("Not enough in the bin for: " + ", ".join(f"{s['product_id']} ({s['bin']})" for s in short))
        self.short = short


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_bin(code: str) -> str:
    """Bin codes as printed on the racking, e.g. A-01-3. Case and spaces around them don't matter."""
    b = (code or "").strip().upper()
    if not BIN.fullmatch(b):
        raise ValueError("A bin code is up to 20 letters, digits and dashes, e.g. A-01-3")
    return b


class Warehouse:
    def __init__(self, db: Database, inventory, warehouse_id: str):
        """inventory: product(ean13) -> {product_id, ean13, name} or None, and restock(store_id, ean13, qty, scan_id,
        recorded_by). Both raise Unavailable when the Inventory Service can't be reached; restock raises Conflict
        when it refuses."""
        self.db = db
        self.inventory = inventory
        self.warehouse_id = warehouse_id

    # -------------------------------------------------------------- products

    def product(self, ean13: str) -> dict:
        if not EAN13.fullmatch(ean13 or ""):
            raise ValueError("A barcode is 13 digits")
        try:
            p = self.inventory.product(ean13)
        except Unavailable:
            cached = self.db.one("SELECT * FROM products WHERE ean13 = ? ORDER BY product_id LIMIT 1", (ean13,))
            if cached:
                return cached
            raise
        if p is None:
            raise NotFound(f"No product with barcode {ean13}. Register it on the store dashboard first.")
        p = {"product_id": p["product_id"], "ean13": p["ean13"], "name": p["name"]}
        with self.db.tx() as c:
            c.execute("INSERT INTO products (product_id, ean13, name) VALUES (?, ?, ?) ON CONFLICT (product_id) "
                      "DO UPDATE SET ean13 = excluded.ean13, name = excluded.name", (p["product_id"], p["ean13"], p["name"]))
        return p

    # ---------------------------------------------------------------- ledger

    def _record(self, c, bin: str, product_id: str, entry_type: str, qty_change: int, key: str, ref: str,
                by: str) -> bool:
        row = c.execute("INSERT INTO ledger (warehouse_id, bin, product_id, entry_type, qty_change, ref, idempotency_key, "
                        "occurred_at, recorded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (idempotency_key) DO NOTHING RETURNING entry_id",
                        (self.warehouse_id, bin, product_id, entry_type, qty_change, ref, key, now_iso(), by)).fetchone()
        return row is not None

    _QTY = "SELECT COALESCE(SUM(qty_change), 0) AS q FROM ledger WHERE warehouse_id = ? AND bin = ? AND product_id = ?"

    def _qty(self, c, bin: str, product_id: str) -> int:
        """Inside a transaction."""
        return c.execute(self._QTY, (self.warehouse_id, bin, product_id)).fetchone()["q"]

    def bin_qty(self, bin: str, product_id: str) -> int:
        return self.db.one(self._QTY, (self.warehouse_id, bin, product_id))["q"]

    def receive(self, ean13: str, qty: int, bin: str, ref: str, scan_id: str, by: str = "") -> dict:
        """Goods in from a supplier, put away in a bin. ref: the supplier's delivery note."""
        if qty <= 0:
            raise ValueError("The quantity received must be at least 1")
        bin = clean_bin(bin)
        p = self.product(ean13)
        with self.db.tx() as c:
            created = self._record(c, bin, p["product_id"], "receive", qty, f"receive:{scan_id}", ref.strip(), by)
        return {"product": p, "bin": bin, "created": created, "bin_qty": self.bin_qty(bin, p["product_id"])}

    def count(self, ean13: str, bin: str, counted_qty: int, count_id: str, by: str = "") -> dict:
        """A stock controller counted a product in a bin. The bin becomes that number; the difference is kept with
        their name. Worked out in one transaction, so a move at the same moment isn't lost in it."""
        if counted_qty < 0:
            raise ValueError("A count can't be below zero")
        bin = clean_bin(bin)
        p = self.product(ean13)
        pid = p["product_id"]
        with self.db.tx() as c:
            was = self._qty(c, bin, pid)
            new = c.execute("INSERT INTO counts (count_id, warehouse_id, bin, product_id, counted_qty, was_qty, counted_by, "
                            "counted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (count_id) DO NOTHING RETURNING count_id",
                            (count_id, self.warehouse_id, bin, pid, counted_qty, was, by, now_iso())).fetchone()
            if new and counted_qty != was:
                self._record(c, bin, pid, "count", counted_qty - was, f"count:{count_id}", count_id, by)
        row = self.db.one("SELECT * FROM counts WHERE count_id = ?", (count_id,))
        if (row["product_id"], row["bin"], row["counted_qty"]) != (pid, bin, counted_qty):
            raise Conflict(f"Count {count_id} was already saved for a different product, bin or number")
        return {"product": p, "bin": bin, "counted": row["counted_qty"], "was": row["was_qty"],
                "difference": row["counted_qty"] - row["was_qty"], "counted_by": row["counted_by"],
                "counted_at": row["counted_at"]}

    def move(self, ean13: str, qty: int, from_bin: str, to_bin: str, move_id: str, by: str = "") -> dict:
        """Move goods from one bin to another. Refused if the first bin doesn't hold that many."""
        if qty <= 0:
            raise ValueError("Move at least 1")
        from_bin, to_bin = clean_bin(from_bin), clean_bin(to_bin)
        if from_bin == to_bin:
            raise ValueError("Pick two different bins")
        p = self.product(ean13)
        pid = p["product_id"]
        with self.db.tx() as c:
            done = c.execute("SELECT 1 AS x FROM ledger WHERE idempotency_key = ?", (f"move:{move_id}:out",)).fetchone()
            if not done:
                have = self._qty(c, from_bin, pid)
                if qty > have:
                    raise NotEnoughStock([{"product_id": pid, "bin": from_bin, "requested": qty, "available": have}])
                self._record(c, from_bin, pid, "move", -qty, f"move:{move_id}:out", move_id, by)
                self._record(c, to_bin, pid, "move", qty, f"move:{move_id}:in", move_id, by)
        return {"product": p, "from_bin": from_bin, "to_bin": to_bin, "moved": qty,
                "from_qty": self.bin_qty(from_bin, pid), "to_qty": self.bin_qty(to_bin, pid)}

    # ------------------------------------------------------------- transfers

    def send(self, transfer_id: str, store_id: str, lines: List[Tuple[str, str, int]], by: str = "") -> dict:
        """Send goods to a store: lines of (ean13, bin, qty). They leave the warehouse now and reach the store's stock
        when the store receives them. All or nothing: refused if any bin is short. Re-sending the same transfer
        changes nothing."""
        if not lines:
            raise ValueError("A transfer needs at least one item")
        if not store_id.strip():
            raise ValueError("Which store is it for?")
        merged: Dict[Tuple[str, str], int] = {}
        for ean13, bin, qty in lines:
            if qty <= 0:
                raise ValueError("Quantities must be at least 1")
            key = (self.product(ean13)["product_id"], clean_bin(bin))
            merged[key] = merged.get(key, 0) + qty
        with self.db.tx() as c:
            existing = c.execute("SELECT * FROM transfers WHERE transfer_id = ?", (transfer_id,)).fetchone()
            if not existing:
                short = []
                for (pid, bin), qty in merged.items():
                    have = self._qty(c, bin, pid)
                    if qty > have:
                        short.append({"product_id": pid, "bin": bin, "requested": qty, "available": have})
                if short:
                    raise NotEnoughStock(short)
                c.execute("INSERT INTO transfers (transfer_id, warehouse_id, store_id, status, created_by, created_at) "
                          "VALUES (?, ?, ?, 'dispatched', ?, ?)", (transfer_id, self.warehouse_id, store_id, by, now_iso()))
                for (pid, bin), qty in merged.items():
                    c.execute("INSERT INTO transfer_lines (transfer_id, product_id, bin, qty) VALUES (?, ?, ?, ?)",
                              (transfer_id, pid, bin, qty))
                    self._record(c, bin, pid, "transfer", -qty, f"transfer:{transfer_id}:{pid}:{bin}", transfer_id, by)
        t = self.transfer(transfer_id)
        if t["store_id"] != store_id or {(l["product_id"], l["bin"]): l["qty"] for l in t["lines"]} != merged:
            raise Conflict(f"Transfer {transfer_id} was already sent with different contents")
        return t

    def receive_at_store(self, transfer_id: str, by: str = "") -> dict:
        """The store has the goods: they're added to its stock (a restock in the Inventory Service, one per product).
        Safe to repeat: if the Inventory Service goes down part-way, receiving again finishes the job without
        counting anything twice."""
        t = self.transfer(transfer_id)
        if t["status"] == "received":
            return t
        per_product: Dict[str, dict] = {}
        for line in t["lines"]:
            p = per_product.setdefault(line["product_id"], {"ean13": line["ean13"], "qty": 0})
            p["qty"] += line["qty"]
        for pid, p in per_product.items():
            self.inventory.restock(t["store_id"], p["ean13"], p["qty"], f"transfer-{transfer_id}-{pid}", by)
        with self.db.tx() as c:
            c.execute("UPDATE transfers SET status = 'received', received_by = ?, received_at = ? "
                      "WHERE transfer_id = ? AND status = 'dispatched'", (by, now_iso(), transfer_id))
        return self.transfer(transfer_id)

    def transfer(self, transfer_id: str) -> dict:
        t = self.db.one("SELECT * FROM transfers WHERE transfer_id = ?", (transfer_id,))
        if not t:
            raise NotFound(f"No transfer {transfer_id}")
        t["lines"] = self.db.all("SELECT tl.product_id, p.name, p.ean13, tl.bin, tl.qty FROM transfer_lines tl "
                                 "JOIN products p ON p.product_id = tl.product_id WHERE tl.transfer_id = ? "
                                 "ORDER BY tl.bin, tl.product_id", (transfer_id,))
        return t

    def transfers(self, status: Optional[str] = None, limit: int = 50) -> List[dict]:
        if status not in (None, "dispatched", "received"):
            raise ValueError("Status is dispatched or received")
        where = "warehouse_id = ?" + (" AND status = ?" if status else "")
        args = (self.warehouse_id, status) if status else (self.warehouse_id,)
        rows = self.db.all(f"SELECT transfer_id FROM transfers WHERE {where} "
                           "ORDER BY created_at DESC, transfer_id DESC LIMIT ?", (*args, limit))
        return [self.transfer(r["transfer_id"]) for r in rows]

    # ----------------------------------------------------------------- views

    def stock(self, bin: Optional[str] = None, ean13: Optional[str] = None) -> List[dict]:
        """What's in each bin (bins with none of a product are left out), by bin then name."""
        where, args = ["l.warehouse_id = ?"], [self.warehouse_id]
        if bin:
            where.append("l.bin = ?")
            args.append(clean_bin(bin))
        if ean13:
            where.append("p.ean13 = ?")
            args.append(ean13)
        return self.db.all(f"SELECT l.product_id, p.name, p.ean13, l.bin, SUM(l.qty_change) AS qty FROM ledger l "
                           f"JOIN products p ON p.product_id = l.product_id WHERE {' AND '.join(where)} "
                           f"GROUP BY l.product_id, p.name, p.ean13, l.bin HAVING SUM(l.qty_change) != 0 "
                           f"ORDER BY l.bin, p.name", args)

    def history(self, limit: int = 50) -> List[dict]:
        return self.db.all("SELECT l.*, p.name FROM ledger l JOIN products p ON p.product_id = l.product_id "
                           "WHERE l.warehouse_id = ? ORDER BY l.entry_id DESC LIMIT ?", (self.warehouse_id, limit))

    def counts(self, limit: int = 50) -> List[dict]:
        return self.db.all("SELECT c.*, p.name FROM counts c JOIN products p ON p.product_id = c.product_id "
                           "WHERE c.warehouse_id = ? ORDER BY c.counted_at DESC, c.count_id LIMIT ?",
                           (self.warehouse_id, limit))
