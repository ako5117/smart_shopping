"""Text customers when their online order moves on.

    paid        the order is paid (for deliveries, with the 4-digit delivery code)
    ready       a collect order is packed and waiting at the counter
    on_the_way  a delivery has left the store with its rider (rider's name and phone, and the code)

The service watches the Inventory and Dispatch Services every few seconds rather than being told by them,
so nothing is missed if it was down for a while, and the other services don't depend on it. Each update
goes into an outbox under a key (kind:order), so it's texted once however often it's seen. Only updates
from the last two hours are texted, so starting the service doesn't text people about old orders.

Sending is retried (after 1, 5 and 15 minutes) when the SMS provider is unreachable or busy; a number that
can't take texts fails straight away. Staff can see every text, and resend one, from the dashboard.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Protocol

import httpx

from . import texts
from .db import Database
from .sms import SendResult

log = logging.getLogger("notify")
RECENT = timedelta(hours=2)
RETRY_AFTER = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15)]


class Sender(Protocol):
    name: str

    def send(self, to: str, body: str) -> SendResult: ...


@dataclass(frozen=True)
class Settings:
    store_id: str = "001"
    store_name: str = "Smart Shopping"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None
    if t is not None and t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


class Notifier:
    def __init__(self, db: Database, sender: Sender, settings: Settings,
                 inventory: httpx.Client, dispatch: Optional[httpx.Client] = None, clock=_now):
        self.db, self.sender, self.settings = db, sender, settings
        self.inventory, self.dispatch, self.clock = inventory, dispatch, clock

    # ------------------------------------------------------------------ outbox

    def queue(self, kind: str, sale_id: str, phone: str, body: str) -> bool:
        """Add a text unless this update was already texted. Returns True if it's new."""
        number = texts.msisdn(phone)
        now = self.clock().isoformat()
        status, error = ("queued", "") if number else ("failed", f"Not a Kenyan mobile number: {phone!r}")
        with self.db.tx() as c:
            row = c.execute(
                "INSERT INTO messages (event_key, kind, sale_id, to_number, body, status, next_attempt_at, error, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (event_key) DO NOTHING RETURNING message_id",
                (f"{kind}:{sale_id}", kind, sale_id, number or phone, body, status, now, error, now)).fetchone()
        return row is not None

    def send_due(self, limit: int = 20) -> int:
        """Send queued texts whose time has come. Returns how many went."""
        now = self.clock()
        due = self.db.all("SELECT * FROM messages WHERE status = 'queued' AND next_attempt_at <= ? "
                          "ORDER BY message_id LIMIT ?", (now.isoformat(), limit))
        sent = 0
        for m in due:
            result = self.sender.send(m["to_number"], m["body"])
            attempts = m["attempts"] + 1
            if result.outcome == "sent":
                fields = {"status": "sent", "provider_ref": result.ref, "cost": result.cost, "error": "",
                          "sent_at": now.isoformat()}
                sent += 1
            elif result.outcome == "retry" and attempts <= len(RETRY_AFTER):
                fields = {"error": result.error, "next_attempt_at": (now + RETRY_AFTER[attempts - 1]).isoformat()}
            else:
                fields = {"status": "failed", "error": result.error}
                log.warning("Text %s to %s failed: %s", m["message_id"], m["to_number"], result.error)
            fields["attempts"] = attempts
            with self.db.tx() as c:
                c.execute(f"UPDATE messages SET {', '.join(f'{k} = ?' for k in fields)} WHERE message_id = ?",
                          (*fields.values(), m["message_id"]))
        return sent

    def resend(self, message_id: int) -> Optional[dict]:
        """Staff: send a text again (failed, or the customer says it never arrived)."""
        with self.db.tx() as c:
            c.execute("UPDATE messages SET status = 'queued', attempts = 0, error = '', next_attempt_at = ? "
                      "WHERE message_id = ? AND to_number LIKE ?", (self.clock().isoformat(), message_id, "+254%"))
        return self.db.one("SELECT * FROM messages WHERE message_id = ?", (message_id,))

    def messages(self, limit: int = 50, sale_id: Optional[str] = None) -> List[dict]:
        if sale_id:
            return self.db.all("SELECT * FROM messages WHERE sale_id = ? ORDER BY message_id DESC LIMIT ?",
                               (sale_id, limit))
        return self.db.all("SELECT * FROM messages ORDER BY message_id DESC LIMIT ?", (limit,))

    # ----------------------------------------------------------------- watcher

    def _recent(self, ts: Optional[str]) -> bool:
        t = _parse(ts)
        return t is not None and self.clock() - t <= RECENT

    def _delivery(self, sale_id: str) -> Optional[dict]:
        r = self.dispatch.get(f"/deliveries/{sale_id}", params={"include_pin": True})
        return r.json() if r.status_code == 200 else None

    def _rider_phone(self, rider_id: str) -> str:
        r = self.dispatch.get(f"/riders/{rider_id}")
        return r.json().get("phone", "") if r.status_code == 200 else ""

    def watch(self) -> int:
        """Look for orders that moved on, and queue their texts. Returns how many were queued."""
        store, queued = self.settings.store_name, 0
        r = self.inventory.get("/sales", params={"store_id": self.settings.store_id, "channel": "online",
                                                 "status": "paid", "limit": 200})
        r.raise_for_status()
        for sale in r.json():
            phone, code = sale.get("customer_phone") or "", sale["sale_id"][-8:]
            if not phone:
                continue
            total = sum((it.get("unit_price_kes") or 0) * it["qty"] for it in sale.get("items", [])) \
                + (sale.get("delivery_fee_kes") or 0)
            is_delivery = sale.get("fulfilment") == "delivery"
            if self._recent(sale.get("updated_at")) and not self._seen("paid", sale["sale_id"]):
                pin = None
                if is_delivery:
                    if self.dispatch is None:
                        continue
                    d = self._delivery(sale["sale_id"])
                    if not d:
                        continue  # try again next time
                    pin = d.get("pin")
                queued += self.queue("paid", sale["sale_id"], phone, texts.paid(store, code, total, pin))
            if not is_delivery and sale.get("ready_at") and not sale.get("exited_at") \
                    and self._recent(sale["ready_at"]) and not self._seen("ready", sale["sale_id"]):
                queued += self.queue("ready", sale["sale_id"], phone, texts.ready(store, code))

        if self.dispatch is not None:
            r = self.dispatch.get("/deliveries", params={"store_id": self.settings.store_id, "status": "picked_up"})
            r.raise_for_status()
            for d in r.json():
                if not self._recent(d.get("picked_up_at")) or self._seen("on_the_way", d["sale_id"]):
                    continue
                full = self._delivery(d["sale_id"])
                if not full or not full.get("pin"):
                    continue
                body = texts.on_the_way(store, d["sale_id"][-8:], d.get("rider_id") or "your rider",
                                        self._rider_phone(d["rider_id"]) if d.get("rider_id") else "", full["pin"])
                queued += self.queue("on_the_way", d["sale_id"], d["customer_phone"], body)
        return queued

    def _seen(self, kind: str, sale_id: str) -> bool:
        return self.db.one("SELECT 1 AS x FROM messages WHERE event_key = ?", (f"{kind}:{sale_id}",)) is not None

    def tick(self) -> None:
        """One round: find updates, then send. Each half carries on if the other fails."""
        try:
            self.watch()
        except httpx.HTTPError as e:
            log.warning("Couldn't check orders: %s", e)
        self.send_due()
