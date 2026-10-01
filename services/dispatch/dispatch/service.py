"""Deliveries of online orders by the store's riders.

A delivery follows its order:

    awaiting_payment -> unassigned -> assigned -> picked_up -> delivered
                    \\-> cancelled                          \\-> failed -> picked_up (try again)

- awaiting_payment: the order exists but isn't paid. It becomes unassigned when the Inventory Service shows
  it paid, or cancelled when the order was cancelled. Checked whenever deliveries are read.
- unassigned / assigned: staff assign a rider, or a rider on shift accepts the job. Either can be undone
  until the rider has the parcel.
- picked_up: staff handed the parcel to the rider. This records the order leaving the store in the
  Inventory Service, like the exit check does for a customer.
- delivered: the rider entered the customer's 4-digit delivery code. Five wrong codes lock the delivery
  until staff look into it, so a rider can't guess their way to "delivered".
- failed: the rider couldn't deliver (customer not answering, wrong address...). Staff call the customer
  and send the rider again.
"""

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Protocol, Tuple

from .db import Database

MAX_PIN_ATTEMPTS = 5
UNPAID_EXPIRY = timedelta(hours=2)  # an order still unpaid after this is never going to be
ACTIVE = ("unassigned", "assigned", "picked_up", "failed")
STATUSES = ("awaiting_payment", "unassigned", "assigned", "picked_up", "delivered", "failed", "cancelled")

# Demo delivery areas around a Nairobi store, with fees in KES. Set DELIVERY_AREAS to the store's own.
DEFAULT_AREAS = "Kilimani:150,Kileleshwa:150,Lavington:200,Westlands:200,Upper Hill:200,CBD:250"


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class WrongPin(Exception):
    def __init__(self, attempts_left: int):
        super().__init__(f"That code isn't right. {attempts_left} tries left." if attempts_left
                         else "That code isn't right.")
        self.attempts_left = attempts_left


class Locked(Exception):
    pass


class Inventory(Protocol):
    def get_sale(self, sale_id: str) -> Optional[dict]: ...

    def record_exit(self, sale_id: str, checked_by: str) -> Tuple[bool, str]: ...


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_areas(spec: str) -> Dict[str, int]:
    """"Kilimani:150,Westlands:200" -> {"Kilimani": 150, "Westlands": 200}."""
    areas = {}
    for part in spec.split(","):
        if not part.strip():
            continue
        name, _, fee = part.rpartition(":")
        if not name.strip() or not fee.strip().isdigit():
            raise ValueError(f"Delivery area {part!r} should look like Name:fee, e.g. Kilimani:150")
        areas[name.strip()] = int(fee)
    if not areas:
        raise ValueError("No delivery areas configured")
    return areas


class Dispatch:
    def __init__(self, db: Database, inventory: Inventory, areas: Dict[str, int]):
        self.db, self.inventory, self.areas = db, inventory, areas

    # ---------------------------------------------------------------- riders

    def save_rider(self, rider_id: str, phone: Optional[str] = None, on_shift: Optional[bool] = None) -> dict:
        """Register a rider on first use; update their phone number or shift. Leaving a field out keeps it."""
        if phone is not None:
            digits = re.sub(r"[\s-]", "", phone)
            if digits and not re.fullmatch(r"\+?\d{9,13}", digits):
                raise ValueError("Enter a phone number like 0712 345 678")
            phone = digits
        with self.db.tx() as c:
            c.execute("INSERT INTO riders (rider_id, phone, on_shift, updated_at) VALUES (?, ?, ?, ?) "
                      "ON CONFLICT (rider_id) DO UPDATE SET "
                      "phone = CASE WHEN ? THEN excluded.phone ELSE riders.phone END, "
                      "on_shift = CASE WHEN ? THEN excluded.on_shift ELSE riders.on_shift END, "
                      "updated_at = excluded.updated_at",
                      (rider_id, phone or "", int(bool(on_shift)), now_iso(), phone is not None, on_shift is not None))
        return self.rider(rider_id)

    def rider(self, rider_id: str) -> dict:
        r = self.db.one("SELECT * FROM riders WHERE rider_id = ?", (rider_id,))
        if not r:
            raise NotFound(f"No rider {rider_id}")
        return _rider(r)

    def riders(self) -> List[dict]:
        rows = self.db.all("SELECT r.*, (SELECT COUNT(*) FROM deliveries d WHERE d.rider_id = r.rider_id "
                           "AND d.status IN ('assigned', 'picked_up', 'failed')) AS active_jobs "
                           "FROM riders r ORDER BY r.on_shift DESC, r.rider_id")
        return [_rider(r) for r in rows]

    # ------------------------------------------------------------ deliveries

    def create(self, sale_id: str, store_id: str, customer_name: str, customer_phone: str, area: str,
               address: str, notes: str = "", lat: Optional[float] = None, lng: Optional[float] = None) -> dict:
        """Called when the online order is placed, before payment. The fee comes from the area."""
        if area not in self.areas:
            raise ValueError(f"We don't deliver to {area}")
        if not address.strip():
            raise ValueError("Enter the delivery address")
        if (lat is None) != (lng is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180)):
            raise ValueError("Location must have both latitude and longitude")
        existing = self.db.one("SELECT * FROM deliveries WHERE sale_id = ?", (sale_id,))
        if existing:
            return self.get(sale_id, include_pin=True)
        ts = now_iso()
        pin = f"{secrets.randbelow(10000):04d}"
        with self.db.tx() as c:
            c.execute("INSERT INTO deliveries (sale_id, store_id, status, customer_name, customer_phone, area, address, "
                      "notes, lat, lng, fee_kes, pin, created_at, updated_at) "
                      "VALUES (?, ?, 'awaiting_payment', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (sale_id, store_id, customer_name.strip(), customer_phone.strip(), area, address.strip(),
                       notes.strip(), lat, lng, self.areas[area], pin, ts, ts))
            _event(c, sale_id, "created", customer_name.strip())
        return self.get(sale_id, include_pin=True)

    def sync_payments(self, store_id: Optional[str] = None) -> None:
        """Move deliveries whose order has been paid (or cancelled) on from awaiting_payment."""
        where, args = "status = 'awaiting_payment'", ()
        if store_id:
            where, args = where + " AND store_id = ?", (store_id,)
        expired_before = (datetime.now(timezone.utc) - UNPAID_EXPIRY).isoformat()
        for d in self.db.all(f"SELECT sale_id, created_at FROM deliveries WHERE {where}", args):
            sale = self.inventory.get_sale(d["sale_id"])
            status = {"paid": "unassigned", "cancelled": "cancelled"}.get((sale or {}).get("status"))
            if sale and sale.get("status") == "pending_payment" and d["created_at"] < expired_before:
                status = "cancelled"  # abandoned before paying
            if status:
                with self.db.tx() as c:
                    c.execute("UPDATE deliveries SET status = ?, updated_at = ? WHERE sale_id = ? "
                              "AND status = 'awaiting_payment'", (status, now_iso(), d["sale_id"]))
                    _event(c, d["sale_id"], "paid" if status == "unassigned" else "cancelled")

    def get(self, sale_id: str, include_pin: bool = False) -> dict:
        d = self.db.one("SELECT * FROM deliveries WHERE sale_id = ?", (sale_id,))
        if d and d["status"] == "awaiting_payment":
            self.sync_payments(d["store_id"])
            d = self.db.one("SELECT * FROM deliveries WHERE sale_id = ?", (sale_id,))
        if not d:
            raise NotFound(f"No delivery for order {sale_id}")
        return self._view(d, include_pin)

    def list(self, store_id: str, statuses: Optional[List[str]] = None, rider_id: Optional[str] = None,
             limit: int = 200) -> List[dict]:
        self.sync_payments(store_id)
        statuses = statuses or list(ACTIVE)
        bad = set(statuses) - set(STATUSES)
        if bad:
            raise ValueError(f"Unknown status {', '.join(sorted(bad))}")
        where = f"store_id = ? AND status IN ({', '.join('?' for _ in statuses)})"
        args = [store_id, *statuses]
        if rider_id:
            where += " AND rider_id = ?"
            args.append(rider_id)
        rows = self.db.all(f"SELECT * FROM deliveries WHERE {where} ORDER BY created_at LIMIT ?", (*args, limit))
        return [self._view(d) for d in rows]

    def events(self, sale_id: str) -> List[dict]:
        return self.db.all("SELECT what, who, detail, at FROM delivery_events WHERE sale_id = ? ORDER BY event_id",
                           (sale_id,))

    # ---------------------------------------------------------------- moves

    def assign(self, sale_id: str, rider_id: str, by: str) -> dict:
        """Staff give the job to a rider (or move it to another), until it's picked up."""
        self.rider(rider_id)
        d = self._current(sale_id)
        if d["status"] not in ("unassigned", "assigned"):
            raise Conflict(self._why_not(d, "assign a rider to"))
        if not self._move(sale_id, d["status"], "assigned", "assigned", by, rider_id,
                          rider_id=rider_id, assigned_at=now_iso()):
            raise Conflict("The delivery changed while you were assigning it. Look again.")
        return self.get(sale_id)

    def claim(self, sale_id: str, rider_id: str) -> dict:
        """A rider on shift accepts an unassigned job. First come, first served."""
        if not self.rider(rider_id)["on_shift"]:
            raise Conflict("Start your shift first")
        self._current(sale_id)
        if not self._move(sale_id, "unassigned", "assigned", "accepted", rider_id, "",
                          rider_id=rider_id, assigned_at=now_iso()):
            raise Conflict("Another rider took this job")
        return self.get(sale_id)

    def release(self, sale_id: str, by: str, rider_id: Optional[str] = None) -> dict:
        """Give the job back before pickup: by the rider it was assigned to, or by staff."""
        d = self._current(sale_id)
        if d["status"] != "assigned" or (rider_id and d["rider_id"] != rider_id):
            raise Conflict(self._why_not(d, "release"))
        self._move(sale_id, "assigned", "unassigned", "released", by, d["rider_id"], rider_id=None, assigned_at=None)
        return self.get(sale_id)

    def hand_over(self, sale_id: str, by: str) -> dict:
        """Staff gave the packed order to its rider. The order leaves the store in the Inventory Service."""
        d = self._current(sale_id)
        if d["status"] != "assigned":
            raise Conflict(self._why_not(d, "hand over"))
        ok, message = self.inventory.record_exit(sale_id, f"{by} (to rider {d['rider_id']})")
        if not ok:
            raise Conflict(message)
        self._move(sale_id, "assigned", "picked_up", "picked_up", by, d["rider_id"],
                   picked_up_at=now_iso(), handed_over_by=by)
        return self.get(sale_id)

    def deliver(self, sale_id: str, rider_id: str, pin: str) -> dict:
        d = self._current(sale_id)
        if d["rider_id"] != rider_id or d["status"] != "picked_up":
            raise Conflict(self._why_not(d, "deliver"))
        if d["pin_attempts"] >= MAX_PIN_ATTEMPTS:
            raise Locked("Too many wrong codes. Call the store.")
        if not secrets.compare_digest(pin.strip(), d["pin"]):
            with self.db.tx() as c:
                c.execute("UPDATE deliveries SET pin_attempts = pin_attempts + 1, updated_at = ? WHERE sale_id = ?",
                          (now_iso(), sale_id))
                _event(c, sale_id, "wrong_code", rider_id)
            left = MAX_PIN_ATTEMPTS - d["pin_attempts"] - 1
            if left <= 0:
                raise Locked("Too many wrong codes. Call the store.")
            raise WrongPin(left)
        self._move(sale_id, "picked_up", "delivered", "delivered", rider_id, "", delivered_at=now_iso())
        return self.get(sale_id)

    def fail(self, sale_id: str, rider_id: str, reason: str) -> dict:
        d = self._current(sale_id)
        if d["rider_id"] != rider_id or d["status"] != "picked_up":
            raise Conflict(self._why_not(d, "report a problem with"))
        if len(reason.strip()) < 3:
            raise ValueError("Say what went wrong")
        self._move(sale_id, "picked_up", "failed", "failed", rider_id, reason.strip(),
                   failed_at=now_iso(), fail_reason=reason.strip())
        return self.get(sale_id)

    def retry(self, sale_id: str, by: str) -> dict:
        """Staff spoke to the customer: the rider tries again. Also unlocks the delivery code."""
        d = self._current(sale_id)
        if d["status"] not in ("failed", "picked_up"):
            raise Conflict(self._why_not(d, "send out again"))
        self._move(sale_id, d["status"], "picked_up", "retry", by, "", pin_attempts=0, fail_reason="")
        return self.get(sale_id)

    # -------------------------------------------------------------- helpers

    def _current(self, sale_id: str) -> dict:
        self.get(sale_id)  # brings awaiting_payment up to date
        return self.db.one("SELECT * FROM deliveries WHERE sale_id = ?", (sale_id,))

    def _move(self, sale_id: str, from_status: str, to_status: str, what: str, who: str, detail: str,
              **fields) -> bool:
        """Change status only if it is still from_status (two people can't both win a race)."""
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.db.tx() as c:
            cur = c.execute(f"UPDATE deliveries SET status = ?, updated_at = ?{', ' + sets if sets else ''} "
                            "WHERE sale_id = ? AND status = ?",
                            (to_status, now_iso(), *fields.values(), sale_id, from_status))
            if cur.rowcount == 0:
                return False
            _event(c, sale_id, what, who, detail)
        return True

    @staticmethod
    def _why_not(d: dict, action: str) -> str:
        state = {"awaiting_payment": "isn't paid yet", "unassigned": "has no rider yet",
                 "assigned": f"is assigned to {d['rider_id']} and not picked up yet",
                 "picked_up": f"is out with {d['rider_id']}", "delivered": "was already delivered",
                 "failed": f"couldn't be delivered by {d['rider_id']}", "cancelled": "was cancelled"}[d["status"]]
        return f"Can't {action} this delivery: it {state}."

    @staticmethod
    def _view(d: dict, include_pin: bool = False) -> dict:
        out = {k: v for k, v in d.items() if k != "pin"}
        out["locked"] = d["pin_attempts"] >= MAX_PIN_ATTEMPTS
        if include_pin:
            out["pin"] = d["pin"]
        return out


def _rider(r: dict) -> dict:
    return {**r, "on_shift": bool(r["on_shift"])}


def _event(c, sale_id: str, what: str, who: str = "", detail: str = "") -> None:
    c.execute("INSERT INTO delivery_events (sale_id, what, who, detail, at) VALUES (?, ?, ?, ?, ?)",
              (sale_id, what, who, detail, now_iso()))
