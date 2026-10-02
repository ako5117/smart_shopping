"""Payment attempts, stored in SQLite (development, tests) or the shared PostgreSQL database (see sqldb.py)."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from .sqldb import Database

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
    updated_at TEXT NOT NULL,
    email TEXT,
    card_brand TEXT,
    card_last4 TEXT,
    provider_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_sale ON payments(sale_id);
"""

# Card payments (method 'card') arrived after the first release; ALTER TABLE brings older databases up to date.
ADDED_COLUMNS = {"payments": [("email", "TEXT"), ("card_brand", "TEXT"), ("card_last4", "TEXT"),
                              ("provider_ref", "TEXT")]}

FINAL_STATUSES = {"paid", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaymentStore:
    """A SQLite path, or a postgresql:// URL (tables in schema "payments" of the shared database)."""

    def __init__(self, url: str, schema: str = "payments"):
        self.db = Database(url, SCHEMA, schema=schema, added_columns=ADDED_COLUMNS)
        self._tx = self.db.tx

    def create(self, sale_id: str, amount: int, phone_number: str, method: str = "mpesa",
               email: Optional[str] = None) -> dict:
        payment_id = str(uuid.uuid4())
        now = _now()
        with self._tx() as c:
            c.execute(
                "INSERT INTO payments (payment_id, sale_id, method, status, amount, phone_number, email, created_at,"
                " updated_at) VALUES (?, ?, ?, 'initiating', ?, ?, ?, ?, ?)",
                (payment_id, sale_id, method, amount, phone_number, email, now, now),
            )
        return self.get(payment_id)

    def mark_card_pending(self, payment_id: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE payments SET status='pending', updated_at=? WHERE payment_id=?", (_now(), payment_id))

    def apply_card_result(self, payment_id: str, status: str, result_desc: str, brand: Optional[str],
                          last4: Optional[str], provider_ref: Optional[str]) -> Optional[dict]:
        """Record a card payment's final result. Idempotent, like apply_result."""
        with self._tx() as c:
            c.execute(
                "UPDATE payments SET status=?, result_desc=?, card_brand=?, card_last4=?, provider_ref=?, updated_at=?"
                " WHERE payment_id=? AND method='card' AND status NOT IN ('paid', 'failed', 'cancelled')",
                (status, (result_desc or "")[:500], brand, last4, provider_ref, _now(), payment_id),
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
        return self.db.one("SELECT * FROM payments WHERE payment_id=?", (payment_id,))

    def get_by_checkout(self, checkout_request_id: str) -> Optional[dict]:
        return self.db.one("SELECT * FROM payments WHERE checkout_request_id=?", (checkout_request_id,))
