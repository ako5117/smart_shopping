"""Scan & Go: customers scan products with their phone, pay with M-Pesa, and show the receipt at the exit.

    uvicorn scan_and_go.app:create_app --factory --port 8030

The browser only talks to this app. Prices come from the Inventory Service, which records each item's
price on the sale; the total charged is worked out from that, never taken from the phone. Stock leaves the ledger only when the Payments Service confirms payment
(it commits the sale in the Inventory Service).
"""

import io
import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

import httpx
import segno
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .airport import Airport, FlightBoard, NotAllowed
from . import account, i18n
from .config import Settings, load_settings

log = logging.getLogger("scan_and_go")
STATIC = Path(__file__).parent / "static"
ORDER_ID = re.compile(r"^SG-[0-9A-F]{16}$")
PAYMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REFRESH_AFTER_S = 15  # ask Daraja directly if no callback has arrived by then


def ean13_valid(code: str) -> bool:
    if not re.fullmatch(r"\d{13}", code):
        return False
    digits = [int(c) for c in code]
    check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12])) % 10) % 10
    return check == digits[12]


def phone_key(phone: str) -> str:
    """Normalise a Kenyan number for rate limiting (the Payments Service does the real validation)."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    return digits


def mask_phone(msisdn: str) -> str:
    return msisdn[:6] + "***" + msisdn[-3:] if len(msisdn) >= 10 else "***"


class Line(BaseModel):
    product_id: str = Field(..., min_length=1, max_length=64)
    qty: int = Field(..., ge=1)


class TravellerIn(BaseModel):
    """Airport mode: the boarding pass barcode as scanned, or the flight details typed in."""
    boarding_pass: Optional[str] = Field(None, max_length=800)
    name: str = Field("", max_length=60)
    flight: str = Field("", max_length=12)
    to: str = Field("", max_length=3)
    date: Optional[str] = Field(None, max_length=10)


class CheckoutIn(BaseModel):
    method: Literal["mpesa", "card"] = "mpesa"
    phone: Optional[str] = Field(None, min_length=9, max_length=16, description="M-Pesa")
    email: Optional[str] = Field(None, max_length=120, description="Card: the receipt goes here")
    items: List[Line] = Field(..., min_length=1)
    traveller: Optional[TravellerIn] = Field(None, description="Airport mode: who's flying where (required there)")
    use_points: bool = Field(False, description="A signed-in customer spends points (whole blocks) on this purchase")


class PayIn(BaseModel):
    method: Literal["mpesa", "card"] = "mpesa"
    phone: Optional[str] = Field(None, min_length=9, max_length=16)
    email: Optional[str] = Field(None, max_length=120)


class RateLimiter:
    def __init__(self, limit: int, window_s: int):
        self.limit, self.window_s = limit, window_s
        self.hits: Dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


class Catalogue:
    """Products, barcodes and prices from the Inventory Service, cached briefly so each scan stays fast."""

    def __init__(self, inventory: httpx.Client, ttl_s: int = 30):
        self.inventory, self.ttl_s = inventory, ttl_s
        self._loaded_at = -ttl_s
        self._by_id: Dict[str, dict] = {}
        self._by_ean: Dict[str, dict] = {}
        self.lock = threading.Lock()

    def _load(self) -> None:
        r = self.inventory.get("/products")
        r.raise_for_status()
        products = [{"product_id": p["product_id"], "ean13": p["ean13"], "name": p["name"],
                     # On offer: the offer price is charged (the Inventory Service saves it on the sale)
                     "price": p.get("sale_price_kes") or p.get("price_kes"),
                     "was_price": p.get("price_kes") if p.get("sale_price_kes") else None}
                    for p in r.json()]
        self._by_id = {p["product_id"]: p for p in products}
        self._by_ean = {p["ean13"]: p for p in products}

    def _fresh(self) -> None:
        with self.lock:
            if time.monotonic() - self._loaded_at >= self.ttl_s:
                self._load()
                self._loaded_at = time.monotonic()

    def by_ean(self, ean13: str) -> Optional[dict]:
        self._fresh()
        return self._by_ean.get(ean13)

    def by_id(self, product_id: str) -> Optional[dict]:
        self._fresh()
        return self._by_id.get(product_id)


class ReportIn(BaseModel):
    kind: Literal["order", "store", "app", "idea"]
    message: str = Field(..., min_length=5, max_length=2000)
    order_id: str = Field("", max_length=40)
    contact: str = Field("", max_length=120)
    context: Dict[str, str] = Field(default_factory=dict)


def create_app(settings: Optional[Settings] = None, inventory: Optional[httpx.Client] = None,
               payments: Optional[httpx.Client] = None, customers: Optional[httpx.Client] = None) -> FastAPI:
    settings = settings or load_settings()
    if customers is None and settings.customers_url:
        customers = httpx.Client(base_url=settings.customers_url, timeout=10.0)
    report_limiter = RateLimiter(6, 3600)
    accounts = account.Accounts(customers)
    if settings.store_mode not in ("shop", "airport"):
        raise ValueError("STORE_MODE should be shop or airport")
    airport = Airport(settings.airport_code, FlightBoard(settings.flights_file), settings.store_tz) \
        if settings.store_mode == "airport" else None

    def traveller_from(t: Optional[TravellerIn]) -> Optional[dict]:
        """Airport mode: check the boarding pass or typed flight again (never trust what the phone kept)."""
        if airport is None:
            return None
        if t is None:
            raise HTTPException(422, "Scan your boarding pass first.")
        try:
            return airport.from_boarding_pass(t.boarding_pass) if t.boarding_pass else airport.from_typed(t.name, t.flight, t.to, t.date)
        except NotAllowed as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        except (OSError, KeyError) as e:  # the flight list couldn't be read
            log.error("Flight information unavailable: %s", e)
            raise HTTPException(503, "Flight details can't be checked right now. Please pay at a till.")
    inventory = inventory or httpx.Client(base_url=settings.inventory_url, timeout=10.0)
    payments = payments or httpx.Client(base_url=settings.payments_url, timeout=40.0)
    catalogue = Catalogue(inventory)
    limiter = RateLimiter(settings.stk_limit_per_phone, settings.stk_limit_window_s)
    app = FastAPI(title="Smart Shopping - Scan & Go")
    i18n.install(app)

    def upstream(name: str, fn):
        try:
            return fn()
        except httpx.HTTPError as e:
            log.error("%s unavailable: %s", name, e)
            raise HTTPException(503, "The store system is not reachable right now. Please pay at a till.")

    def start_payment(sale_id: str, phone: str, amount: int) -> dict:
        if not limiter.allow(phone_key(phone)):
            raise HTTPException(429, "Too many payment requests for this number. Please wait a few minutes.")
        r = upstream("Payments", lambda: payments.post("/payments/mpesa/stk-push",
                                                        json={"sale_id": sale_id, "phone": phone, "amount": amount}))
        if r.status_code == 422:
            raise HTTPException(422, "Enter a valid Safaricom number, e.g. 0712 345 678.")
        if r.status_code >= 400:
            log.error("STK push for %s failed: %s %s", sale_id, r.status_code, r.text[:200])
            raise HTTPException(502, "M-Pesa could not send the payment request. Please try again.")
        return r.json()

    methods = {"at": None, "card": False}

    def card_available() -> bool:
        """Whether the Payments Service takes cards (checked at most once a minute)."""
        if methods["at"] is None or time.monotonic() - methods["at"] > 60:
            try:
                r = payments.get("/payments/methods")
                methods["card"] = r.status_code == 200 and bool(r.json().get("card"))
            except httpx.HTTPError:
                methods["card"] = False
            methods["at"] = time.monotonic()
        return methods["card"]

    def start_card_payment(sale_id: str, email: Optional[str], amount: int, request: Request) -> dict:
        """A hosted card checkout page; the customer's phone comes back to Scan & Go afterwards."""
        if not card_available():
            raise HTTPException(503, "Card payments aren't available right now. Please pay with M-Pesa or at a till.")
        if not email or "@" not in email:
            raise HTTPException(422, "Enter your email address: the card receipt goes there.")
        r = upstream("Payments", lambda: payments.post("/payments/card/checkout", json={
            "sale_id": sale_id, "amount": amount, "email": email.strip(),
            "return_url": f"{str(request.base_url).rstrip('/')}/?order={sale_id}"}))
        if r.status_code == 422:
            raise HTTPException(422, "Enter a valid email address for the card receipt.")
        if r.status_code >= 400:
            log.error("Card checkout for %s failed: %s %s", sale_id, r.status_code, r.text[:200])
            raise HTTPException(502, "Card payment couldn't start. Please try again, or pay with M-Pesa.")
        return r.json()

    def pay(sale_id: str, method: str, phone: Optional[str], email: Optional[str], amount: int, request: Request) -> dict:
        if method == "card":
            return start_card_payment(sale_id, email, amount, request)
        if not phone:
            raise HTTPException(422, "Enter your M-Pesa number.")
        return start_payment(sale_id, phone, amount)

    def priced_sale(sale: dict) -> dict:
        lines = []
        for it in sale["items"]:
            p = catalogue.by_id(it["product_id"]) or {"name": it["product_id"], "price": None}
            price = it.get("unit_price_kes") or p["price"]  # the price saved on the sale wins
            lines.append({"product_id": it["product_id"], "name": p["name"], "qty": it["qty"], "price": price,
                          "line_total": (price or 0) * it["qty"]})
        points_off = sale.get("loyalty_discount_kes") or 0
        return {"lines": lines, "points_discount": points_off, "total": sum(l["line_total"] for l in lines) - points_off}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/reports", status_code=201)
    def report(body: ReportIn, request: Request):
        """Report a problem (the Customers Service keeps it; staff answer on the dashboard's Reports page)."""
        if customers is None:
            raise HTTPException(404, "Problem reports aren't set up in this store. Please ask a member of staff.")
        who = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
        if not report_limiter.allow(who):
            raise HTTPException(429, "Thanks: we've had several reports from you in the last hour. Please ask a member of staff.")
        context = {}
        if body.kind == "app":  # only app problems carry the page and browser, to help fix them
            context = {k[:40]: v[:500] for k, v in list(body.context.items())[:12]}
            context["user_agent"] = request.headers.get("user-agent", "")[:300]
        try:
            r = customers.post("/reports", json={**body.model_dump(), "app": "scan_and_go", "context": context})
        except httpx.HTTPError as e:
            log.error("Customers Service unavailable: %s", e)
            raise HTTPException(503, "Reports can't be sent right now. Please ask a member of staff.")
        if r.status_code != 201:
            raise HTTPException(422 if r.status_code == 422 else 502, "Please check what you wrote and try again.")
        out = r.json()
        return {k: out[k] for k in ("report_id", "kind_label", "status")}

    account.install(app, accounts, RateLimiter(10, 900))

    @app.get("/api/store")
    def store():
        return {"store_id": settings.store_id, "name": settings.store_name, "currency": "KES", "card": card_available(),
                "reports": customers is not None, "mode": settings.store_mode,
                "airport": settings.airport_code if airport else None,
                **({"tz": settings.store_tz} if airport else {})}

    @app.post("/api/traveller")
    def traveller(body: TravellerIn):
        """Airport mode: check a boarding pass (or typed flight) before shopping. Nothing is kept until checkout."""
        if airport is None:
            raise HTTPException(404, "This store isn't in an airport")
        return traveller_from(body)

    @app.get("/api/products/{ean13}")
    def product(ean13: str):
        if not ean13_valid(ean13):
            raise HTTPException(422, "That isn't a valid barcode. Try scanning again.")
        p = upstream("Inventory", lambda: catalogue.by_ean(ean13))
        if not p:
            raise HTTPException(404, "This product isn't in Scan & Go yet. Please pay for it at a till.")
        if p["price"] is None:
            raise HTTPException(409, f"{p['name']} has no Scan & Go price yet. Please pay for it at a till.")
        return p

    @app.post("/api/checkout", status_code=201)
    def checkout(body: CheckoutIn, request: Request):
        merged: Dict[str, int] = {}
        for line in body.items:
            merged[line.product_id] = merged.get(line.product_id, 0) + line.qty
        if len(merged) > settings.max_lines:
            raise HTTPException(422, f"Scan & Go takes up to {settings.max_lines} different products.")
        lines = []
        for pid, qty in merged.items():
            if qty > settings.max_qty_per_item:
                raise HTTPException(422, f"For more than {settings.max_qty_per_item} of one product, please use a till.")
            p = upstream("Inventory", lambda: catalogue.by_id(pid))
            if not p or p["price"] is None:
                raise HTTPException(409, "Something in your basket can't be bought with Scan & Go. Please remove it.")
            lines.append((p, qty))
        flyer = traveller_from(body.traveller)
        sale_id = "SG-" + secrets.token_hex(8).upper()
        # A signed-in customer: link the sale to their account (it earns points once paid), spending points if asked.
        linked = accounts.start_order(request, sale_id, sum(p["price"] * qty for p, qty in lines), body.use_points)
        points_off = (linked or {}).get("discount_kes", 0)

        def give_back():
            if linked:
                accounts.cancel_order(sale_id)

        try:
            r = upstream("Inventory", lambda: inventory.post("/sales", json={
                "sale_id": sale_id, "store_id": settings.store_id, **({"traveller": flyer} if flyer else {}),
                **({"customer_name": flyer["name"]} if flyer else {}),
                "loyalty_discount_kes": points_off, "customer_id": (linked or {}).get("customer_id", ""),
                "items": [{"product_id": p["product_id"], "qty": qty} for p, qty in lines]}))
        except HTTPException:
            give_back()
            raise
        if r.status_code >= 400:
            give_back()
            log.error("Inventory refused sale %s: %s %s", sale_id, r.status_code, r.text[:200])
            raise HTTPException(502, "The store system couldn't start your order. Please pay at a till.")
        # Charge the prices the Inventory Service saved on the sale, not this app's cached copy.
        sale = r.json()
        goods = sum((it.get("unit_price_kes") or 0) * it["qty"] for it in sale.get("items", []))
        if any(not it.get("unit_price_kes") for it in sale.get("items", [])) or points_off >= goods:
            upstream("Inventory", lambda: inventory.post(f"/sales/{sale_id}/cancel"))
            give_back()
            raise HTTPException(409, "Something in your basket can't be bought with Scan & Go. Please remove it.")
        total = goods - points_off

        try:
            payment = pay(sale_id, body.method, body.phone, body.email, total, request)
        except HTTPException:
            upstream("Inventory", lambda: inventory.post(f"/sales/{sale_id}/cancel"))
            give_back()
            raise
        return {"order_id": sale_id, "payment_id": payment["payment_id"], "total": total, "points_discount": points_off,
                "checkout_url": payment.get("checkout_url")}

    @app.get("/api/orders/{order_id}/qr.svg")
    def order_qr(order_id: str):
        """QR code of the order number, scanned by staff at the exit. Holds nothing that isn't on the pass."""
        if not ORDER_ID.match(order_id):
            raise HTTPException(404, "Order not found")
        out = io.BytesIO()
        segno.make(order_id, error="m").save(out, kind="svg", scale=6, border=2, dark="#000", light="#fff", xmldecl=False)
        return Response(out.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/api/orders/{order_id}/pay", status_code=201)
    def pay_again(order_id: str, body: PayIn, request: Request):
        """Try again after a cancelled or failed payment, by M-Pesa or card, for the same order."""
        if not ORDER_ID.match(order_id):
            raise HTTPException(404, "Order not found")
        r = upstream("Inventory", lambda: inventory.get(f"/sales/{order_id}"))
        if r.status_code == 404:
            raise HTTPException(404, "Order not found")
        sale = r.json()
        if sale["status"] != "pending_payment":
            raise HTTPException(409, "This order is already paid." if sale["status"] == "paid" else "This order was cancelled.")
        total = priced_sale(sale)["total"]
        payment = pay(order_id, body.method, body.phone, body.email, total, request)
        return {"order_id": order_id, "payment_id": payment["payment_id"], "total": total,
                "checkout_url": payment.get("checkout_url")}

    @app.get("/api/orders/{order_id}/payments/{payment_id}")
    def order_status(order_id: str, payment_id: str):
        if not ORDER_ID.match(order_id) or not PAYMENT_ID.match(payment_id):
            raise HTTPException(404, "Order not found")
        r = upstream("Payments", lambda: payments.get(f"/payments/{payment_id}"))
        if r.status_code == 404:
            raise HTTPException(404, "Order not found")
        payment = r.json()
        if payment.get("sale_id") != order_id:
            raise HTTPException(404, "Order not found")
        if payment["status"] == "pending" and (payment.get("method") == "card" or _age_s(payment) >= REFRESH_AFTER_S):
            refreshed = upstream("Payments", lambda: payments.post(f"/payments/{payment_id}/refresh"))
            if refreshed.status_code == 200:
                payment = refreshed.json()
        r = upstream("Inventory", lambda: inventory.get(f"/sales/{order_id}"))
        if r.status_code == 404:
            raise HTTPException(404, "Order not found")
        sale = r.json()
        return {
            "order_id": order_id,
            "store_name": settings.store_name,
            "payment_status": payment["status"],  # pending | paid | failed | cancelled
            "payment_method": payment.get("method", "mpesa"),
            "card": f"{payment.get('card_brand') or 'Card'} •••• {payment['card_last4']}" if payment.get("card_last4") else None,
            "sale_status": sale["status"],  # pending_payment | paid | cancelled
            "mpesa_receipt": payment.get("mpesa_receipt_number"),
            "phone": mask_phone(payment.get("phone_number", "")),
            "amount": payment.get("amount"),
            "paid_at": payment.get("updated_at") if payment["status"] == "paid" else None,
            "exited_at": sale.get("exited_at"),
            "traveller": sale.get("traveller") or None,
            "points": accounts.points_for(sale),
            "result": payment.get("result_desc"),
            **priced_sale(sale),
        }

    return app


def _age_s(payment: dict) -> float:
    try:
        created = datetime.fromisoformat(payment["created_at"])
    except (KeyError, ValueError):
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds()
