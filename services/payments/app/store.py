"""SQLite storage for payment attempts. Swapped for the shared database once it is set up."""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    sale_id TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'mpesa',
    status TEXT NOT NULL,
    amount INTEGER NOT NULL,
    phone_number TEXT NOT NULL,
    merchant_request_id TEXT,
    checkout_request_id TEXT UNIQUE,
    mpesa_receipt_number TEXT,
    result_code INTEGER,
    result_desc TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_sale ON payments(sale_id);
"""

FINAL_STATUSES = {"paid", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaymentStore:
    def __init__(self, path: str):
        self.path = path
        # A single shared connection keeps ":memory:" databases alive for tests.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    @contextmanager
    def _tx(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def create(self, sale_id: str, amount: int, phone_number: str) -> dict:
        payment_id = str(uuid.uuid4())
        now = _now()
        with self._tx() as c:
            c.execute(
                "INSERT INTO payments (payment_id, sale_id, status, amount, phone_number, created_at, updated_at)"
                " VALUES (?, ?, 'initiating', ?, ?, ?, ?)",
                (payment_id, sale_id, amount, phone_number, now, now),
            )
        return self.get(payment_id)

    def mark_pending(self, payment_id: str, merchant_request_id: str, checkout_request_id: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE payments SET status='pending', merchant_request_id=?, checkout_request_id=?, updated_at=?"
                " WHERE payment_id=?",
                (merchant_request_id, checkout_request_id, _now(), payment_id),
            )

    def mark_failed_to_start(self, payment_id: str, reason: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE payments SET status='failed', result_desc=?, updated_at=? WHERE payment_id=?",
                (reason[:500], _now(), payment_id),
            )

    def apply_result(
        self,
        checkout_request_id: str,
        status: str,
        result_code: int,
        result_desc: str,
        receipt_number: Optional[str],
    ) -> Optional[dict]:
        """Record a final result. Idempotent: a payment already in a final state is left unchanged."""
        with self._tx() as c:
            c.execute(
                "UPDATE payments SET status=?, result_code=?, result_desc=?, mpesa_receipt_number=?, updated_at=?"
                " WHERE checkout_request_id=? AND status NOT IN ('paid', 'failed', 'cancelled')",
                (status, result_code, result_desc[:500], receipt_number, _now(), checkout_request_id),
            )
        return self.get_by_checkout(checkout_request_id)

    def get(self, payment_id: str) -> Optional[dict]:
        row = self._conn.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        return dict(row) if row else None

    def get_by_checkout(self, checkout_request_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM payments WHERE checkout_request_id=?", (checkout_request_id,)
        ).fetchone()
        return dict(row) if row else None
