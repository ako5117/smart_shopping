"""Customer accounts and loyalty points for the online shop.

Signing up
    The customer gives their name, phone number, email and date of birth, and optionally their gender and a
    photo, and accepts the privacy notice. A 6-digit code goes to their email; entering it confirms the
    address and signs them in. Later sign-ins are the same: email, then the emailed code (no password to
    forget or leak). Codes last CODE_MINUTES, allow MAX_CODE_ATTEMPTS tries, and only a few are sent per
    email in a while. Codes and session tokens are stored only as hashes.

Points (when the store runs the loyalty programme)
    1 point for every KES 100 spent on goods (the delivery fee and any points discount don't count), once
    the order is paid. Points are spent in blocks: 100 points take KES 50 off an order. Both rates are
    settings. Points spent on an order that's then cancelled or expires are given back. The store can turn
    the programme off (LOYALTY_ENABLED=false): accounts and order history keep working, nothing is earned
    or spent, and balances are kept for if it's turned back on.

Deleting an account erases the person's details and photo; the points history stays, without them.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, List, Optional

from . import photos

GENDERS = ("female", "male", "other")
PRIVACY_VERSION = "2026-10"
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class NotFound(LookupError):
    pass


class Conflict(Exception):
    pass


class TooMany(Exception):
    pass


class WrongCode(ValueError):
    def __init__(self, message: str, attempts_left: int = 0):
        super().__init__(message)
        self.attempts_left = attempts_left


@dataclass(frozen=True)
class Settings:
    store_name: str = "Smart Shopping"
    loyalty_enabled: bool = True
    kes_per_point: int = 100     # 1 point per this many KES spent
    redeem_points: int = 100     # points are spent in blocks of this many...
    redeem_kes: int = 50         # ...each taking this much off an order
    code_minutes: int = 10
    max_code_attempts: int = 5
    codes_per_window: int = 3
    code_window_minutes: int = 15
    session_days: int = 30
    min_age: int = 18            # younger customers would need a parent's consent (Data Protection Act)
    abandon_hours: int = 24      # an order the shop never recorded is dropped after this long

    def __post_init__(self):
        if min(self.kes_per_point, self.redeem_points, self.redeem_kes) < 1:
            raise ValueError("Loyalty rates must be at least 1")

    def public(self) -> dict:
        return {"loyalty_enabled": self.loyalty_enabled, "kes_per_point": self.kes_per_point,
                "redeem_points": self.redeem_points, "redeem_kes": self.redeem_kes,
                "min_age": self.min_age, "privacy_version": PRIVACY_VERSION}


LANGUAGES = ("en", "sw")


def _language(lang: Optional[str]) -> str:
    return lang if lang in LANGUAGES else "en"


# The emails customers get, in each language: (subject, body). {code} is filled in when the code is made.
EMAILS = {
    "en": {
        "welcome": ("Confirm your email",
                    "Welcome to {store}, {first}.\n\nYour code is {code}. Enter it in the shop to confirm your email. "
                    "It works for {minutes} minutes.\n\nIf you didn't sign up, ignore this email."),
        "sign_in": ("Your sign-in code",
                    "Your {store} sign-in code is {code}. It works for {minutes} minutes.\n\n"
                    "If you didn't ask for it, ignore this email."),
    },
    "sw": {
        "welcome": ("Thibitisha barua pepe yako",
                    "Karibu {store}, {first}.\n\nMsimbo wako ni {code}. Uweke kwenye duka la mtandaoni ili kuthibitisha "
                    "barua pepe yako. Unafanya kazi kwa dakika {minutes}.\n\nKama hukujisajili, puuza barua pepe hii."),
        "sign_in": ("Msimbo wako wa kuingia",
                    "Msimbo wako wa kuingia {store} ni {code}. Unafanya kazi kwa dakika {minutes}.\n\n"
                    "Kama hukuuomba, puuza barua pepe hii."),
    },
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalise_phone(phone: str) -> str:
    """A Kenyan mobile number as 2547XXXXXXXX or 2541XXXXXXXX, or ValueError."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        digits = "254" + digits
    if not re.fullmatch(r"254[71]\d{8}", digits):
        raise ValueError("Enter a Kenyan mobile number, e.g. 0712 345 678.")
    return digits


def normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL.match(email) or len(email) > 120:
        raise ValueError("Enter a valid email address.")
    return email


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return (name[:1] + "***@" + domain) if domain else "***"


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class Customers:
    def __init__(self, db, mailer, settings: Settings = Settings(), inventory=None,
                 clock: Optional[Callable[[], datetime]] = None):
        self.db, self.mailer, self.settings, self.inventory = db, mailer, settings, inventory
        self.clock = clock or now_utc

    def _now(self) -> str:
        return self.clock().isoformat()

    # ------------------------------------------------------------- sign up / in

    def _check_details(self, name: str, phone: str, dob: str, gender: Optional[str]):
        name = " ".join((name or "").split())
        if not 2 <= len(name) <= 60:
            raise ValueError("Enter your name.")
        phone = normalise_phone(phone)
        try:
            born = date.fromisoformat(dob or "")
        except ValueError:
            raise ValueError("Enter your date of birth.")
        today = self.clock().date()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        if age < self.settings.min_age:
            raise ValueError(f"You need to be {self.settings.min_age} or over to join.")
        if age > 120:
            raise ValueError("Check your date of birth.")
        gender = (gender or "").strip().lower() or None
        if gender is not None and gender not in GENDERS:
            raise ValueError("Choose female, male, other, or leave it blank.")
        return name, phone, born.isoformat(), gender

    def signup(self, name: str, phone: str, email: str, dob: str, gender: Optional[str], consent: bool,
               language: str = "en") -> dict:
        """New account (or one never confirmed, with new details). Sends the code that confirms the email."""
        language = _language(language)
        if not consent:
            raise ValueError("Please read and accept the privacy notice to join.")
        email = normalise_email(email)
        name, phone, dob, gender = self._check_details(name, phone, dob, gender)
        ts = self._now()
        with self.db.tx() as c:
            mine = c.execute("SELECT * FROM customers WHERE email = ?", (email,)).fetchone()
            if mine and mine["email_verified_at"] and not mine["deleted_at"]:
                raise Conflict("There's already an account with this email. Sign in instead.")
            other = c.execute("SELECT * FROM customers WHERE phone = ? AND email <> ?", (phone, email)).fetchone()
            if other and other["email_verified_at"]:
                raise Conflict("That phone number belongs to another account. Sign in with that account's email.")
            if other:  # an unfinished sign-up with someone else's email: the number goes to whoever confirms
                c.execute("UPDATE customers SET phone = NULL WHERE customer_id = ?", (other["customer_id"],))
            if mine:
                c.execute("UPDATE customers SET name = ?, phone = ?, dob = ?, gender = ?, consent_at = ?, consent_version = ?, "
                          "updated_at = ?, deleted_at = NULL, language = ? WHERE customer_id = ?",
                          (name, phone, dob, gender, ts, PRIVACY_VERSION, ts, language, mine["customer_id"]))
            else:
                c.execute("INSERT INTO customers (customer_id, name, phone, email, dob, gender, consent_at, consent_version, "
                          "created_at, updated_at, language) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          ("C" + secrets.token_hex(8).upper(), name, phone, email, dob, gender, ts, PRIVACY_VERSION, ts, ts,
                           language))
        subject, body = EMAILS[language]["welcome"]
        self._send_code(email, subject, body.format(store=self.settings.store_name, first=name.split()[0],
                                                    minutes=self.settings.code_minutes, code="{code}"))
        return {"email": email, "sent": True}

    def request_code(self, email: str, language: Optional[str] = None) -> dict:
        """Sign in: a code to the email, if it has an account. The answer is the same either way. The email is in the
        language the shop is showing (or else the account's)."""
        email = normalise_email(email)
        c = self.db.one("SELECT name, language FROM customers WHERE email = ? AND deleted_at IS NULL", (email,))
        if c:
            subject, body = EMAILS[_language(language or c["language"])]["sign_in"]
            self._send_code(email, subject, body.format(store=self.settings.store_name, minutes=self.settings.code_minutes,
                                                        code="{code}"))
        return {"email": email, "sent": True}

    def _send_code(self, email: str, subject: str, body: str) -> None:
        since = (self.clock() - timedelta(minutes=self.settings.code_window_minutes)).isoformat()
        n = self.db.one("SELECT COUNT(*) AS n FROM codes WHERE email = ? AND created_at >= ?", (email, since))["n"]
        if n >= self.settings.codes_per_window:
            raise TooMany(f"Too many codes sent to this email. Please wait {self.settings.code_window_minutes} minutes.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(8)
        expires = (self.clock() + timedelta(minutes=self.settings.code_minutes)).isoformat()
        with self.db.tx() as c:
            c.execute("UPDATE codes SET used_at = ? WHERE email = ? AND used_at IS NULL", (self._now(), email))  # one live code
            c.execute("INSERT INTO codes (email, salt, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                      (email, salt, _hash(salt, code), expires, self._now()))
        self.mailer.send(email, f"{self.settings.store_name}: {subject}", body.replace("{code}", code))

    def verify(self, email: str, code: str) -> dict:
        """Check an emailed code: confirms the email and signs in. Returns {token, customer}."""
        email = normalise_email(email)
        code = re.sub(r"\D", "", code or "")
        ts = self._now()
        wrong = None
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM codes WHERE email = ? AND used_at IS NULL ORDER BY code_id DESC LIMIT 1",
                            (email,)).fetchone()
            if not row or row["expires_at"] < ts or row["attempts"] >= self.settings.max_code_attempts:
                raise WrongCode("That code has expired. Ask for a new one.")
            if not hmac.compare_digest(row["code_hash"], _hash(row["salt"], code)):
                # Counted, and committed: raising here would roll the count back.
                c.execute("UPDATE codes SET attempts = attempts + 1 WHERE code_id = ?", (row["code_id"],))
                wrong = self.settings.max_code_attempts - row["attempts"] - 1
        if wrong is not None:
            raise WrongCode("That code isn't right." + (f" {wrong} tries left." if wrong else " Ask for a new one."), wrong)
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM codes WHERE code_id = ? AND used_at IS NULL", (row["code_id"],)).fetchone()
            if not row:  # used by a request that got here first
                raise WrongCode("That code has expired. Ask for a new one.")
            c.execute("UPDATE codes SET used_at = ? WHERE code_id = ?", (ts, row["code_id"]))
            cust = c.execute("SELECT * FROM customers WHERE email = ? AND deleted_at IS NULL", (email,)).fetchone()
            if not cust:
                raise NotFound("No account with this email.")
            if not cust["email_verified_at"]:
                c.execute("UPDATE customers SET email_verified_at = ?, updated_at = ? WHERE customer_id = ?",
                          (ts, ts, cust["customer_id"]))
            token = secrets.token_urlsafe(32)
            expires = (self.clock() + timedelta(days=self.settings.session_days)).isoformat()
            c.execute("INSERT INTO sessions (token_hash, customer_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                      (_hash(token), cust["customer_id"], ts, expires))
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (ts,))
        return {"token": token, "expires_at": expires, "customer": self.profile(cust["customer_id"])}

    def customer_for(self, token: str) -> str:
        """The customer_id signed in with this session token, or NotFound."""
        if not token:
            raise NotFound("Not signed in")
        row = self.db.one("SELECT s.customer_id FROM sessions s JOIN customers c ON c.customer_id = s.customer_id "
                          "WHERE s.token_hash = ? AND s.expires_at > ? AND c.deleted_at IS NULL",
                          (_hash(token), self._now()))
        if not row:
            raise NotFound("Not signed in")
        return row["customer_id"]

    def sign_out(self, token: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token or ""),))

    # ----------------------------------------------------------------- profile

    def _row(self, customer_id: str) -> dict:
        row = self.db.one("SELECT * FROM customers WHERE customer_id = ? AND deleted_at IS NULL", (customer_id,))
        if not row:
            raise NotFound("No such customer")
        return row

    def balance(self, customer_id: str) -> int:
        return self.db.one("SELECT COALESCE(SUM(points), 0) AS n FROM points WHERE customer_id = ?", (customer_id,))["n"]

    def loyalty(self, customer_id: str) -> dict:
        s = self.settings
        points = self.balance(customer_id)
        blocks = points // s.redeem_points
        pending = self.db.one("SELECT COUNT(*) AS n FROM orders WHERE customer_id = ? AND status = 'pending'",
                              (customer_id,))["n"]
        return {"enabled": s.loyalty_enabled, "points": points, "worth_kes": blocks * s.redeem_kes if s.loyalty_enabled else 0,
                "to_next": s.redeem_points - points % s.redeem_points, "pending_orders": pending, **s.public()}

    def profile(self, customer_id: str) -> dict:
        r = self._row(customer_id)
        photo = self.db.one("SELECT updated_at FROM customer_photos WHERE customer_id = ?", (customer_id,))
        return {"customer_id": r["customer_id"], "name": r["name"], "phone": r["phone"], "email": r["email"],
                "dob": r["dob"], "gender": r["gender"], "member_since": r["created_at"],
                "email_verified": bool(r["email_verified_at"]),
                "photo_version": _hash(photo["updated_at"])[:12] if photo else None,
                "language": _language(r.get("language")),
                "loyalty": self.loyalty(customer_id)}

    def set_language(self, customer_id: str, language: str) -> dict:
        if language not in LANGUAGES:
            raise ValueError("Choose English (en) or Kiswahili (sw).")
        self._row(customer_id)
        with self.db.tx() as c:
            c.execute("UPDATE customers SET language = ?, updated_at = ? WHERE customer_id = ?", (language, self._now(), customer_id))
        return self.profile(customer_id)

    def update(self, customer_id: str, name: str, phone: str, dob: str, gender: Optional[str]) -> dict:
        name, phone, dob, gender = self._check_details(name, phone, dob, gender)
        with self.db.tx() as c:
            other = c.execute("SELECT customer_id FROM customers WHERE phone = ? AND customer_id <> ?",
                              (phone, customer_id)).fetchone()
            if other:
                raise Conflict("That phone number belongs to another account.")
            c.execute("UPDATE customers SET name = ?, phone = ?, dob = ?, gender = ?, updated_at = ? WHERE customer_id = ?",
                      (name, phone, dob, gender, self._now(), customer_id))
        return self.profile(customer_id)

    def set_photo(self, customer_id: str, data: bytes) -> dict:
        self._row(customer_id)
        jpeg = photos.prepare(data)
        ts = self._now()
        with self.db.tx() as c:
            c.execute("DELETE FROM customer_photos WHERE customer_id = ?", (customer_id,))
            c.execute("INSERT INTO customer_photos (customer_id, data, updated_at) VALUES (?, ?, ?)", (customer_id, jpeg, ts))
        return self.profile(customer_id)

    def photo(self, customer_id: str) -> bytes:
        row = self.db.one("SELECT data FROM customer_photos WHERE customer_id = ?", (customer_id,))
        if not row:
            raise NotFound("No photo")
        return bytes(row["data"])

    def delete_photo(self, customer_id: str) -> dict:
        with self.db.tx() as c:
            c.execute("DELETE FROM customer_photos WHERE customer_id = ?", (customer_id,))
        return self.profile(customer_id)

    def delete(self, customer_id: str) -> None:
        """Erase the person: details, photo, sessions and codes. Orders and points history stay, unnamed."""
        r = self._row(customer_id)
        ts = self._now()
        with self.db.tx() as c:
            c.execute("DELETE FROM customer_photos WHERE customer_id = ?", (customer_id,))
            c.execute("DELETE FROM sessions WHERE customer_id = ?", (customer_id,))
            c.execute("DELETE FROM codes WHERE email = ?", (r["email"],))
            c.execute("UPDATE reports SET contact = '' WHERE customer_id = ?", (customer_id,))
            c.execute("UPDATE customers SET name = '', phone = NULL, email = ?, dob = NULL, gender = NULL, "
                      "updated_at = ?, deleted_at = ? WHERE customer_id = ?",
                      (f"deleted-{customer_id.lower()}@invalid", ts, ts, customer_id))

    # ------------------------------------------------------------------ orders

    def start_order(self, customer_id: str, sale_id: str, goods_kes: int, use_points: bool) -> dict:
        """Link an online order to the customer, spending points on it if asked. Before the sale is recorded.

        The discount is whole blocks of points, never the whole goods total (something is always paid).
        Repeating it for the same order changes nothing.
        """
        s = self.settings
        self._row(customer_id)
        with self.db.tx() as c:
            have = c.execute("SELECT * FROM orders WHERE sale_id = ?", (sale_id,)).fetchone()
            if have:
                if have["customer_id"] != customer_id:
                    raise Conflict("That order belongs to someone else")
                return dict(have)
            blocks = 0
            if use_points and s.loyalty_enabled and goods_kes > s.redeem_kes:
                points = c.execute("SELECT COALESCE(SUM(points), 0) AS n FROM points WHERE customer_id = ?",
                                   (customer_id,)).fetchone()["n"]
                blocks = min(points // s.redeem_points, (goods_kes - 1) // s.redeem_kes)
            ts = self._now()
            c.execute("INSERT INTO orders (sale_id, customer_id, status, redeemed_points, discount_kes, created_at) "
                      "VALUES (?, ?, 'pending', ?, ?, ?)", (sale_id, customer_id, blocks * s.redeem_points, blocks * s.redeem_kes, ts))
            if blocks:
                c.execute("INSERT INTO points (customer_id, sale_id, kind, points, created_at) VALUES (?, ?, 'redeem', ?, ?)",
                          (customer_id, sale_id, -blocks * s.redeem_points, ts))
        return self.order(sale_id)

    def order(self, sale_id: str) -> dict:
        row = self.db.one("SELECT * FROM orders WHERE sale_id = ?", (sale_id,))
        if not row:
            raise NotFound("No such order")
        return row

    def cancel_order(self, sale_id: str) -> dict:
        """The order didn't go ahead (or was cancelled, or expired): give back any points spent on it."""
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM orders WHERE sale_id = ?", (sale_id,)).fetchone()
            if not row:
                raise NotFound("No such order")
            if row["status"] == "pending":
                self._close(c, row, "cancelled")
        return self.order(sale_id)

    def _close(self, c, row: dict, status: str, earned: int = 0) -> None:
        ts = self._now()
        c.execute("UPDATE orders SET status = ?, earned_points = ?, settled_at = ? WHERE sale_id = ? AND status = 'pending'",
                  (status, earned, ts, row["sale_id"]))
        if status == "cancelled" and row["redeemed_points"]:
            c.execute("INSERT INTO points (customer_id, sale_id, kind, points, created_at) VALUES (?, ?, 'refund', ?, ?) "
                      "ON CONFLICT (sale_id, kind) DO NOTHING", (row["customer_id"], row["sale_id"], row["redeemed_points"], ts))
        if earned:
            c.execute("INSERT INTO points (customer_id, sale_id, kind, points, created_at) VALUES (?, ?, 'earn', ?, ?) "
                      "ON CONFLICT (sale_id, kind) DO NOTHING", (row["customer_id"], row["sale_id"], earned, ts))

    def earned_for(self, sale: dict) -> int:
        """Points for a paid sale: goods actually paid for (not the delivery fee), per KES_PER_POINT."""
        if not self.settings.loyalty_enabled:
            return 0
        goods = sum((it.get("unit_price_kes") or 0) * it["qty"] for it in sale.get("items", []))
        return max(0, goods - (sale.get("loyalty_discount_kes") or 0)) // self.settings.kes_per_point

    def settle(self, customer_id: Optional[str] = None) -> int:
        """Look at pending orders in the Inventory Service: paid ones earn points, cancelled ones give points back."""
        if self.inventory is None:
            return 0
        q, args = "SELECT * FROM orders WHERE status = 'pending'", []
        if customer_id:
            q, args = q + " AND customer_id = ?", [customer_id]
        settled = 0
        old = (self.clock() - timedelta(hours=self.settings.abandon_hours)).isoformat()
        for row in self.db.all(q + " ORDER BY created_at LIMIT 200", args):
            try:
                sale = self.inventory.sale(row["sale_id"])
            except Exception:  # the Inventory Service is down: try again next time
                return settled
            if sale is None and row["created_at"] >= old:
                continue  # not recorded yet (or the shop is still placing it)
            status = (sale or {}).get("status")
            if status == "pending_payment":
                continue
            with self.db.tx() as c:
                if status == "paid":
                    self._close(c, row, "paid", self.earned_for(sale))
                else:  # cancelled, or never recorded
                    self._close(c, row, "cancelled")
            settled += 1
        return settled

    def orders(self, customer_id: str, limit: int = 20) -> List[dict]:
        return self.db.all("SELECT * FROM orders WHERE customer_id = ? AND status <> 'cancelled' ORDER BY created_at DESC LIMIT ?",
                           (customer_id, limit))

    # --------------------------------------------------------------- dashboard

    def summary(self) -> dict:
        one = self.db.one
        live = "deleted_at IS NULL AND email_verified_at IS NOT NULL"
        return {"members": one(f"SELECT COUNT(*) AS n FROM customers WHERE {live}")["n"],
                "points_outstanding": one("SELECT COALESCE(SUM(points), 0) AS n FROM points")["n"],
                "points_earned": one("SELECT COALESCE(SUM(points), 0) AS n FROM points WHERE kind = 'earn'")["n"],
                "points_redeemed": -one("SELECT COALESCE(SUM(points), 0) AS n FROM points WHERE kind IN ('redeem', 'refund')")["n"],
                "discount_given_kes": one("SELECT COALESCE(SUM(discount_kes), 0) AS n FROM orders WHERE status = 'paid'")["n"],
                **self.settings.public()}

    def members(self, limit: int = 50) -> List[dict]:
        """Newest members first. Names and points, not birthdays, gender or full email addresses."""
        rows = self.db.all(
            "SELECT c.customer_id, c.name, c.email, c.created_at, "
            "(SELECT COALESCE(SUM(points), 0) FROM points p WHERE p.customer_id = c.customer_id) AS points, "
            "(SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.customer_id AND o.status = 'paid') AS orders "
            "FROM customers c WHERE c.deleted_at IS NULL AND c.email_verified_at IS NOT NULL "
            "ORDER BY c.created_at DESC LIMIT ?", (limit,))
        return [{**r, "email": mask_email(r["email"])} for r in rows]

    def emails(self, limit: int = 30) -> List[dict]:
        return self.db.all("SELECT * FROM emails ORDER BY email_id DESC LIMIT ?", (limit,))
