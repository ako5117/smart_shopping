"""Problem reports from customers: something wrong with an order, something in the store, the app not working,
or an idea. Sent from the online shop or Scan & Go, with or without an account; answered by staff on the
dashboard's Reports page.

App problems carry a little context the customer doesn't have to type: which app and page, the browser, and the
last errors the page saw. A photo or screenshot can be added (re-encoded, metadata dropped). Signed-in customers
see their reports and the store's reply on their Account tab; others get a reference and, if they left a phone
number or email, a call or message back.
"""

import json
import re
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from . import photos
from .service import NotFound

KINDS = {"order": "A problem with an order", "store": "Something in the store", "app": "The app isn't working",
         "idea": "An idea or suggestion"}
APPS = ("store", "scan_and_go")
STATUSES = ("open", "in_progress", "resolved")
ORDER_ID = re.compile(r"^(WEB|SG)-[0-9A-F]{6,24}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Reports:
    def __init__(self, db):
        self.db = db

    def create(self, kind: str, message: str, app: str, order_id: str = "", contact: str = "", context: Optional[dict] = None,
               customer_id: Optional[str] = None, photo: Optional[bytes] = None) -> dict:
        if kind not in KINDS:
            raise ValueError("Choose what the problem is about")
        if app not in APPS:
            raise ValueError("Unknown app")
        message = (message or "").strip()
        if not 5 <= len(message) <= 2000:
            raise ValueError("Tell us what happened (a few words at least)")
        order_id = (order_id or "").strip().upper()
        if order_id and not ORDER_ID.match(order_id):
            raise ValueError("That order number doesn't look right")
        contact = " ".join((contact or "").split())[:120]
        ctx = json.dumps({k: str(v)[:500] for k, v in (context or {}).items()}, ensure_ascii=False)[:4000] if kind == "app" or context else ""
        jpeg = photos.prepare_snapshot(photo) if photo else None
        report_id = "R-" + secrets.token_hex(5).upper()
        ts = _now()
        with self.db.tx() as c:
            c.execute("INSERT INTO reports (report_id, kind, message, app, order_id, customer_id, contact, context, status, "
                      "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                      (report_id, kind, message, app, order_id, customer_id, contact, ctx, ts, ts))
            if jpeg:
                c.execute("INSERT INTO report_photos (report_id, data) VALUES (?, ?)", (report_id, jpeg))
        return self.get(report_id)

    def get(self, report_id: str) -> dict:
        r = self.db.one("SELECT r.*, (SELECT COUNT(*) FROM report_photos p WHERE p.report_id = r.report_id) AS has_photo "
                        "FROM reports r WHERE r.report_id = ?", (report_id,))
        if not r:
            raise NotFound("No such report")
        return self._shape(r)

    def _shape(self, r: dict) -> dict:
        r["has_photo"] = bool(r.get("has_photo"))
        r["kind_label"] = KINDS[r["kind"]]
        try:
            r["context"] = json.loads(r["context"]) if r["context"] else {}
        except ValueError:
            r["context"] = {}
        return r

    def photo(self, report_id: str) -> bytes:
        row = self.db.one("SELECT data FROM report_photos WHERE report_id = ?", (report_id,))
        if not row:
            raise NotFound("No photo")
        return bytes(row["data"])

    def public(self, report_id: str) -> dict:
        """For whoever has the reference (someone who reported without an account): status and reply only."""
        r = self.get(report_id)
        return {k: r[k] for k in ("report_id", "kind", "kind_label", "status", "reply", "created_at", "updated_at")}

    def for_customer(self, customer_id: str, limit: int = 20) -> List[dict]:
        """What the customer sees: their reports and the store's reply (not the staff names or page details)."""
        rows = self.db.all("SELECT report_id, kind, message, order_id, status, reply, created_at, updated_at FROM reports "
                           "WHERE customer_id = ? ORDER BY created_at DESC LIMIT ?", (customer_id, limit))
        return [{**r, "kind_label": KINDS[r["kind"]]} for r in rows]

    def list(self, statuses: Optional[List[str]] = None, limit: int = 100) -> List[dict]:
        statuses = statuses or ["open", "in_progress"]
        if any(s not in STATUSES for s in statuses):
            raise ValueError("Unknown report status")
        marks = ",".join("?" * len(statuses))
        rows = self.db.all(
            "SELECT r.*, (SELECT COUNT(*) FROM report_photos p WHERE p.report_id = r.report_id) AS has_photo, "
            "c.name AS customer_name FROM reports r LEFT JOIN customers c ON c.customer_id = r.customer_id "
            f"WHERE r.status IN ({marks}) ORDER BY r.created_at DESC LIMIT ?", (*statuses, limit))
        return [self._shape(r) for r in rows]

    def counts(self) -> dict:
        rows = self.db.all("SELECT status, COUNT(*) AS n FROM reports GROUP BY status")
        out = {s: 0 for s in STATUSES}
        out.update({r["status"]: r["n"] for r in rows})
        return out

    def update(self, report_id: str, by: str, status: Optional[str] = None, reply: Optional[str] = None) -> dict:
        r = self.get(report_id)
        status = status or r["status"]
        if status not in STATUSES:
            raise ValueError("Unknown report status")
        reply = r["reply"] if reply is None else " ".join(reply.split())[:1000]
        if status == "resolved" and not reply:
            raise ValueError("Tell the customer what was done before marking it resolved")
        with self.db.tx() as c:
            c.execute("UPDATE reports SET status = ?, reply = ?, handled_by = ?, updated_at = ? WHERE report_id = ?",
                      (status, reply, by, _now(), report_id))
        return self.get(report_id)
