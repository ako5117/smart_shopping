"""Online storefront: customers order online, pay with M-Pesa and collect from the store.

    uvicorn storefront.app:create_app --factory --port 8040

Availability is live. For each product the shop promises only:

    committed stock (Inventory Service)
    - items held by unpaid orders, online or Scan & Go (Inventory Service, for 15 minutes)
    - items shoppers in the store picked up in the last few minutes (shelf sensors)
    - a small buffer, in case the shelf count is off

When something runs low or out, the shop suggests substitutes from the same category. The Inventory
Service checks again when the order is placed, in the same transaction that records it, so two customers
can't both buy the last one. Prices and totals come from the Inventory Service, never from the browser.
Paid orders appear on the store dashboard to be packed. The customer collects with the pass on their
phone, or a rider delivers (Dispatch Service). Delivery is express or in a scheduled time slot, priced by
the distance from the store; the Dispatch Service quotes the fee and says whether the address is too far.
The customer gives the rider a 4-digit code shown on their order page.
"""

import io
import logging
import os
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

from . import account, i18n, reports
from .config import Settings, load_settings
from .shelf import picked_up, read_recent_lines

log = logging.getLogger("storefront")
STATIC = Path(__file__).parent / "static"
ORDER_ID = re.compile(r"^WEB-[0-9A-F]{16}$")
PRODUCT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PAYMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REFRESH_AFTER_S = 15  # ask Daraja directly if no callback has arrived by then
MAX_SUBSTITUTES = 3


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


class DeliveryIn(BaseModel):
    address: str = Field(..., min_length=3, max_length=200)
    notes: str = Field("", max_length=300)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    tier: Literal["express", "scheduled"] = "scheduled"
    slot_start: Optional[str] = Field(None, max_length=40)


class OrderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, description="Who collects or receives the order")
    phone: str = Field(..., min_length=9, max_length=16)
    items: List[Line] = Field(..., min_length=1)
    fulfilment: Literal["collect", "delivery"] = "collect"
    delivery: Optional[DeliveryIn] = None
    payment_method: Literal["mpesa", "card"] = "mpesa"
    email: Optional[str] = Field(None, max_length=120, description="Card payments: the card receipt goes here")
    use_points: bool = Field(False, description="Signed-in customers: spend loyalty points on this order")


class ReturnLineIn(BaseModel):
    product_id: str = Field(..., max_length=64)
    qty: int = Field(..., ge=1, le=100)


class ReturnIn(BaseModel):
    lines: List[ReturnLineIn] = Field(..., min_length=1, max_length=30)
    reason: str = Field(..., min_length=3, max_length=300)
    unopened: bool = False


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


class Cached:
    """A value fetched at most once every ttl_s seconds."""

    def __init__(self, fetch, ttl_s: float):
        self.fetch, self.ttl_s = fetch, ttl_s
        self._at, self._value = None, None
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if self._at is None or time.monotonic() - self._at >= self.ttl_s:
                self._value = self.fetch()
                self._at = time.monotonic()
            return self._value

    def clear(self) -> None:
        with self.lock:
            self._at = None


def stock_status(available: int, few_left: int) -> str:
    return "out" if available <= 0 else "few" if available <= few_left else "in"


def substitutes(product: dict, products: List[dict], available: Dict[str, int]) -> List[dict]:
    """In-stock products from the same category, closest in price first."""
    if not product.get("category"):
        return []
    options = [p for p in products if p["category"] == product["category"]
               and p["product_id"] != product["product_id"] and available.get(p["product_id"], 0) > 0]
    options.sort(key=lambda p: (abs(p["price"] - product["price"]), p["name"]))
    return [{"product_id": p["product_id"], "name": p["name"], "price": p["price"]} for p in options[:MAX_SUBSTITUTES]]


def create_app(settings: Optional[Settings] = None, inventory: Optional[httpx.Client] = None,
               payments: Optional[httpx.Client] = None, clock=None, dispatch: Optional[httpx.Client] = None,
               customers: Optional[httpx.Client] = None) -> FastAPI:
    settings = settings or load_settings()
    inventory = inventory or httpx.Client(base_url=settings.inventory_url, timeout=10.0)
    payments = payments or httpx.Client(base_url=settings.payments_url, timeout=40.0)
    if dispatch is None and settings.dispatch_url:
        dispatch = httpx.Client(base_url=settings.dispatch_url, timeout=10.0)
    if customers is None and settings.customers_url:
        customers = httpx.Client(base_url=settings.customers_url, timeout=10.0)
    clock = clock or (lambda: datetime.now(timezone.utc))
    limiter = RateLimiter(settings.stk_limit_per_phone, settings.stk_limit_window_s)
    returns_limiter = RateLimiter(5, 3600)
    accounts = account.Accounts(customers)
    app = FastAPI(title="Smart Shopping - Online store")
    i18n.install(app)

    def upstream(name: str, fn):
        try:
            return fn()
        except httpx.HTTPError as e:
            log.error("%s unavailable: %s", name, e)
            raise HTTPException(503, "The shop can't reach the store right now. Please try again in a minute.")

    def fetch_products() -> List[dict]:
        r = inventory.get("/products")
        r.raise_for_status()
        return [{"product_id": p["product_id"], "name": p["name"], "category": p.get("category") or "",
                 # On offer: the offer price is what's charged; the shelf price is shown crossed out.
                 "price": p.get("sale_price_kes") or p["price_kes"],
                 "was_price": p["price_kes"] if p.get("sale_price_kes") else None,
                 "offer": _offer_label(p.get("discount")) if p.get("sale_price_kes") else None,
                 # The photo staff added on the dashboard; the version in the address lets phones cache it for good.
                 "image_url": f"api/products/{p['product_id']}/image?v={p['image_version']}" if p.get("image_version") else None}
                for p in r.json() if p.get("price_kes")]

    def fetch_held() -> Dict[str, dict]:
        r = inventory.get(f"/availability/{settings.store_id}")
        r.raise_for_status()
        return r.json()

    def fetch_delivery() -> dict:
        r = dispatch.get("/delivery")
        r.raise_for_status()
        return r.json()

    delivery_cache = Cached(fetch_delivery, settings.products_cache_s)

    def delivery_settings() -> Optional[dict]:
        """How far the store delivers and how it's priced; None when delivery is off or the Dispatch Service is down."""
        if dispatch is None:
            return None
        try:
            return delivery_cache.get()
        except httpx.HTTPError as e:
            log.warning("Dispatch unavailable: %s", e)
            return None

    def delivery_quote(lat: float, lng: float) -> dict:
        """Fees and open slots for this location, from the Dispatch Service."""
        if dispatch is None:
            raise HTTPException(503, "This store doesn't deliver. Choose collect from the store.")
        try:
            r = dispatch.get("/delivery/quote", params={"lat": lat, "lng": lng})
        except httpx.HTTPError as e:
            log.warning("Dispatch unavailable: %s", e)
            raise HTTPException(503, "Delivery isn't available right now. Choose collect from the store, or try again later.")
        if r.status_code == 422:
            raise HTTPException(422, "That location isn't valid. Try finding it on the map again.")
        if r.status_code != 200:
            log.error("Delivery quote failed: %s %s", r.status_code, r.text[:200])
            raise HTTPException(503, "Delivery isn't available right now. Choose collect from the store, or try again later.")
        return r.json()

    def delivery_for(sale: dict) -> Optional[dict]:
        if sale.get("fulfilment") != "delivery" or dispatch is None:
            return None
        try:
            r = dispatch.get(f"/deliveries/{sale['sale_id']}", params={"include_pin": True})
            if r.status_code != 200:
                return None
            d = r.json()
            rider = None
            if d.get("rider_id"):
                rr = dispatch.get(f"/riders/{d['rider_id']}")
                rider = {"name": d["rider_id"], "phone": rr.json().get("phone", "") if rr.status_code == 200 else ""}
        except httpx.HTTPError as e:
            log.warning("Dispatch unavailable: %s", e)
            return None
        return {"status": d["status"], "tier": d.get("tier") or "scheduled", "slot": d.get("slot_label") or "",
                "distance_km": d.get("distance_km"), "address": d["address"], "notes": d["notes"],
                "fee": d["fee_kes"], "pin": d["pin"] if sale["status"] == "paid" else None, "rider": rider,
                "picked_up_at": d.get("picked_up_at"), "delivered_at": d.get("delivered_at")}

    def fetch_methods() -> dict:
        r = payments.get("/payments/methods")
        r.raise_for_status()
        return r.json()

    methods_cache = Cached(fetch_methods, settings.products_cache_s)

    def card_available() -> bool:
        try:
            return bool(methods_cache.get().get("card"))
        except httpx.HTTPError as e:
            log.warning("Payments unavailable: %s", e)
            return False

    products_cache = Cached(fetch_products, settings.products_cache_s)
    held_cache = Cached(fetch_held, settings.stock_cache_s)
    shelf_cache = Cached(lambda: read_recent_lines(settings.shelf_events_path), settings.stock_cache_s)

    def live_availability() -> dict:
        """Per product: how many the shop can promise right now."""
        ledger = upstream("Inventory", held_cache.get)
        try:
            in_baskets = picked_up(shelf_cache.get(), settings.picked_window_min, clock())
            shelf_live = bool(settings.shelf_events_path) and os.path.exists(settings.shelf_events_path)
        except OSError as e:
            log.warning("Shelf events unavailable: %s", e)
            in_baskets, shelf_live = {}, False
        available = {pid: max(0, a["stock"] - a["held"] - in_baskets.get(pid, 0) - settings.in_store_buffer)
                     for pid, a in ledger.items()}
        return {"available": available, "in_baskets": in_baskets, "shelf_live": shelf_live}

    def priced_sale(sale: dict, names: Dict[str, str]) -> dict:
        lines = [{"product_id": it["product_id"], "name": names.get(it["product_id"], it["product_id"]),
                  "qty": it["qty"], "price": it.get("unit_price_kes"),
                  "line_total": (it.get("unit_price_kes") or 0) * it["qty"]} for it in sale["items"]]
        fee = sale.get("delivery_fee_kes") or 0
        points_off = sale.get("loyalty_discount_kes") or 0
        return {"lines": lines, "delivery_fee": fee, "points_discount": points_off,
                "total": sum(l["line_total"] for l in lines) + fee - points_off}

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

    def start_card_payment(sale_id: str, email: Optional[str], amount: int, request: Request) -> dict:
        """Get a hosted card checkout page for this order; the customer comes back to the shop afterwards."""
        if not card_available():
            raise HTTPException(503, "Card payments aren't available right now. Please pay with M-Pesa.")
        if not email or "@" not in email:
            raise HTTPException(422, "Enter your email address: the card receipt goes there.")
        return_url = f"{str(request.base_url).rstrip('/')}/?order={sale_id}"
        r = upstream("Payments", lambda: payments.post("/payments/card/checkout", json={
            "sale_id": sale_id, "amount": amount, "email": email.strip(), "return_url": return_url}))
        if r.status_code == 422:
            raise HTTPException(422, "Enter a valid email address for the card receipt.")
        if r.status_code >= 400:
            log.error("Card checkout for %s failed: %s %s", sale_id, r.status_code, r.text[:200])
            raise HTTPException(502, "Card payment couldn't start. Please try again, or pay with M-Pesa.")
        return r.json()

    def not_enough(short: List[dict], products: List[dict], available: Dict[str, int]):
        by_id = {p["product_id"]: p for p in products}
        detail = [{**s, "name": by_id.get(s["product_id"], {}).get("name", s["product_id"]),
                   "substitutes": substitutes(by_id[s["product_id"]], products, available) if s["product_id"] in by_id else []}
                  for s in short]
        names = ", ".join(d["name"] for d in detail)
        raise HTTPException(409, {"message": f"Not enough in stock: {names}. Change your basket and try again.",
                                  "short": detail})

    def sale_total(sale: dict) -> int:
        """What the customer pays: the goods, plus any delivery fee, less any points discount."""
        return (sum((it.get("unit_price_kes") or 0) * it["qty"] for it in sale["items"]) + (sale.get("delivery_fee_kes") or 0)
                - (sale.get("loyalty_discount_kes") or 0))

    def online_sale(order_id: str) -> dict:
        if not ORDER_ID.match(order_id):
            raise HTTPException(404, "Order not found")
        r = upstream("Inventory", lambda: inventory.get(f"/sales/{order_id}"))
        if r.status_code == 404 or (r.status_code == 200 and r.json().get("channel") != "online"):
            raise HTTPException(404, "Order not found")
        r.raise_for_status()
        return r.json()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/store")
    def store():
        delivery = delivery_settings()
        loyalty = accounts.settings()
        return {"store_id": settings.store_id, "name": settings.store_name, "currency": "KES",
                "hold_minutes": settings.order_hold_min, "card": card_available(),
                "delivery": {"available": bool(delivery), **(delivery or {})},
                "accounts": {"available": accounts.on, **(loyalty or {})}}

    @app.get("/api/delivery/quote")
    def quote(lat: float, lng: float, request: Request):
        """Can we deliver here, and for how much? Asked before shopping and again at checkout."""
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise HTTPException(422, "That location isn't valid. Try finding it on the map again.")
        q, lang = delivery_quote(lat, lng), i18n.language(request)
        out = {**q, "reason": i18n.translate(q.get("reason") or "", lang)}
        if q.get("scheduled"):
            out["scheduled"] = {**q["scheduled"], "slots": [{**s, "label": i18n.translate(s["label"], lang)}
                                                            for s in q["scheduled"].get("slots", [])]}
        return out

    @app.get("/api/catalogue")
    def catalogue(request: Request):
        lang = i18n.language(request)
        products = upstream("Inventory", products_cache.get)
        live = live_availability()
        available = live["available"]
        out = []
        for p in products:
            n = available.get(p["product_id"], 0)
            status = stock_status(n, settings.few_left)
            out.append({**p, "offer": i18n.translate(p["offer"], lang) if p.get("offer") else p.get("offer"),
                        "status": status, "left": n if status == "few" else None,
                        "max": min(n, settings.max_qty_per_item),
                        "substitutes": substitutes(p, products, available) if status != "in" else []})
        out.sort(key=lambda p: (p["category"] or "~", p["name"]))
        return {"products": out, "categories": sorted({p["category"] for p in products if p["category"]}),
                "shelf_live": live["shelf_live"], "updated_at": clock().isoformat()}

    @app.post("/api/orders", status_code=201)
    def place_order(body: OrderIn, request: Request):
        merged: Dict[str, int] = {}
        for line in body.items:
            merged[line.product_id] = merged.get(line.product_id, 0) + line.qty
        if len(merged) > settings.max_lines:
            raise HTTPException(422, f"An online order takes up to {settings.max_lines} different products.")
        if any(q > settings.max_qty_per_item for q in merged.values()):
            raise HTTPException(422, f"Up to {settings.max_qty_per_item} of one product per order, please.")
        products = upstream("Inventory", products_cache.get)
        by_id = {p["product_id"]: p for p in products}
        if any(pid not in by_id for pid in merged):
            raise HTTPException(409, "Something in your basket isn't sold online any more. Please remove it.")
        fee = 0
        if body.fulfilment == "delivery":
            if body.delivery is None:
                raise HTTPException(422, "Enter where to deliver.")
            d = body.delivery
            q = delivery_quote(d.lat, d.lng)  # the fee comes from here, never from the browser
            if not q["deliverable"]:
                raise HTTPException(422, q["reason"])
            option = q[d.tier]
            if not option["available"]:
                raise HTTPException(422, "Express delivery has closed for today. Choose a delivery time instead."
                                    if d.tier == "express" else "No delivery times are left. Choose express or collect.")
            if d.tier == "scheduled" and not any(_same_time(s["start"], d.slot_start) for s in option["slots"]):
                raise HTTPException(422, "That delivery time isn't available any more. Choose another.")
            fee = option["fee_kes"]

        # Fresh numbers for the check, not the few-seconds-old copy the page was showing.
        held_cache.clear()
        shelf_cache.clear()
        available = live_availability()["available"]
        short = [{"product_id": pid, "requested": q, "available": available.get(pid, 0)}
                 for pid, q in merged.items() if q > available.get(pid, 0)]
        if short:
            not_enough(short, products, available)

        sale_id = "WEB-" + secrets.token_hex(8).upper()
        # Signed in: the order earns points, and can spend them (the discount is worked out by the Customers Service).
        goods = sum(by_id[pid]["price"] * q for pid, q in merged.items())
        linked = accounts.start_order(request, sale_id, goods, body.use_points)
        points_off = (linked or {}).get("discount_kes", 0)

        def abandon():
            upstream("Inventory", lambda: inventory.post(f"/sales/{sale_id}/cancel"))
            held_cache.clear()
            if linked:
                accounts.cancel_order(sale_id)

        try:
            r = upstream("Inventory", lambda: inventory.post("/sales", json={
                "sale_id": sale_id, "store_id": settings.store_id, "channel": "online", "customer_name": body.name.strip(),
                "customer_phone": body.phone.strip(),
                "check_stock": True, "fulfilment": body.fulfilment, "delivery_fee_kes": fee,
                "loyalty_discount_kes": points_off, "customer_id": (linked or {}).get("customer_id", ""),
                "language": i18n.language(request),  # the texts about this order come in it
                "items": [{"product_id": pid, "qty": q} for pid, q in merged.items()]}))
        except HTTPException:
            if linked:
                accounts.cancel_order(sale_id)
            raise
        if r.status_code >= 400 and linked:
            accounts.cancel_order(sale_id)
        if r.status_code == 409 and isinstance(r.json().get("detail"), dict):
            held_cache.clear()
            not_enough(r.json()["detail"]["short"], products, available)
        if r.status_code >= 400:
            log.error("Inventory refused order %s: %s %s", sale_id, r.status_code, r.text[:200])
            raise HTTPException(502, "The store couldn't take your order. Please try again.")
        sale = r.json()
        held_cache.clear()
        if any(not it.get("unit_price_kes") for it in sale["items"]):
            abandon()
            raise HTTPException(409, "Something in your basket isn't sold online any more. Please remove it.")
        total = sale_total(sale)
        if body.fulfilment == "delivery":
            d = body.delivery
            try:
                r = dispatch.post("/deliveries", json={
                    "sale_id": sale_id, "store_id": settings.store_id, "customer_name": body.name.strip(),
                    "customer_phone": body.phone.strip(), "tier": d.tier, "slot_start": d.slot_start,
                    "address": d.address, "notes": d.notes, "lat": d.lat, "lng": d.lng})
                ok = r.status_code == 201
                if not ok:
                    log.error("Dispatch refused delivery %s: %s %s", sale_id, r.status_code, r.text[:200])
            except httpx.HTTPError as e:
                log.error("Dispatch unavailable for %s: %s", sale_id, e)
                ok = False
            if not ok:
                abandon()
                raise HTTPException(503, "Delivery isn't available right now. Choose collect from the store, or try again later.")
        try:
            if body.payment_method == "card":
                payment = start_card_payment(sale_id, body.email, total, request)
            else:
                payment = start_payment(sale_id, body.phone, total)
        except HTTPException:
            abandon()
            raise
        return {"order_id": sale_id, "payment_id": payment["payment_id"], "total": total, "points_discount": points_off,
                "checkout_url": payment.get("checkout_url")}

    @app.post("/api/orders/{order_id}/pay", status_code=201)
    def pay_again(order_id: str, body: PayIn, request: Request):
        """Try again after a cancelled or failed payment, by M-Pesa or card, while the order still holds its items."""
        sale = online_sale(order_id)
        if sale["status"] != "pending_payment":
            raise HTTPException(409, "This order is already paid." if sale["status"] == "paid" else "This order was cancelled.")
        if _age_s(sale, clock()) > settings.order_hold_min * 60:
            upstream("Inventory", lambda: inventory.post(f"/sales/{order_id}/cancel"))
            raise HTTPException(409, "This order has expired and its items were released. Please place it again.")
        total = sale_total(sale)
        if body.method == "card":
            payment = start_card_payment(order_id, body.email, total, request)
        elif body.phone:
            payment = start_payment(order_id, body.phone, total)
        else:
            raise HTTPException(422, "Enter your M-Pesa number.")
        return {"order_id": order_id, "payment_id": payment["payment_id"], "total": total,
                "checkout_url": payment.get("checkout_url")}

    @app.get("/api/orders/{order_id}/payments/{payment_id}")
    def order_status(order_id: str, payment_id: str, request: Request):
        if not ORDER_ID.match(order_id) or not PAYMENT_ID.match(payment_id):
            raise HTTPException(404, "Order not found")
        r = upstream("Payments", lambda: payments.get(f"/payments/{payment_id}"))
        if r.status_code == 404:
            raise HTTPException(404, "Order not found")
        payment = r.json()
        if payment.get("sale_id") != order_id:
            raise HTTPException(404, "Order not found")
        # Cards: ask the provider straight away (the customer has just come back from the card page).
        # M-Pesa: give the callback time to arrive before asking Daraja.
        if payment["status"] == "pending" and (payment.get("method") == "card" or _age_s(payment, clock()) >= REFRESH_AFTER_S):
            refreshed = upstream("Payments", lambda: payments.post(f"/payments/{payment_id}/refresh"))
            if refreshed.status_code == 200:
                payment = refreshed.json()
        sale = online_sale(order_id)
        delivery = delivery_for(sale)
        if delivery and delivery.get("slot"):
            delivery["slot"] = i18n.translate(delivery["slot"], i18n.language(request))
        if sale["status"] == "cancelled":
            stage = "cancelled"
        elif sale["status"] == "paid" and sale.get("fulfilment") == "delivery":
            stage = {"picked_up": "on_the_way", "failed": "on_the_way", "delivered": "delivered"}.get(
                (delivery or {}).get("status"), "ready" if sale.get("ready_at") else "packing")
        elif sale["status"] == "paid":
            stage = "collected" if sale.get("exited_at") else "ready" if sale.get("ready_at") else "packing"
        else:
            stage = "awaiting_payment" if payment["status"] == "pending" else "payment_failed"
        names = {p["product_id"]: p["name"] for p in upstream("Inventory", products_cache.get)}
        return {
            "order_id": order_id,
            "store_name": settings.store_name,
            "customer_name": sale.get("customer_name", ""),
            "stage": stage,  # awaiting_payment | payment_failed | packing | ready | collected | on_the_way | delivered | cancelled
            "fulfilment": sale.get("fulfilment", "collect"),
            "delivery": delivery,
            "payment_status": payment["status"],
            "payment_method": payment.get("method", "mpesa"),
            "card": f"{payment.get('card_brand') or 'Card'} •••• {payment['card_last4']}" if payment.get("card_last4") else None,
            "mpesa_receipt": payment.get("mpesa_receipt_number"),
            "phone": mask_phone(payment.get("phone_number", "")),
            "amount": payment.get("amount"),
            "paid_at": payment.get("updated_at") if payment["status"] == "paid" else None,
            "ready_at": sale.get("ready_at"),
            "collected_at": sale.get("exited_at"),
            "result": payment.get("result_desc"),
            "points": points_for(sale),
            **priced_sale(sale, names),
        }

    def own_order(order_id: str, payment_id: str) -> dict:
        """The order, if this payment is one of its own: the order page's address is the customer's key."""
        if not ORDER_ID.match(order_id) or not PAYMENT_ID.match(payment_id):
            raise HTTPException(404, "Order not found")
        r = upstream("Payments", lambda: payments.get(f"/payments/{payment_id}"))
        if r.status_code != 200 or r.json().get("sale_id") != order_id:
            raise HTTPException(404, "Order not found")
        return online_sale(order_id)

    @app.get("/api/orders/{order_id}/payments/{payment_id}/returns")
    def order_returns(order_id: str, payment_id: str, request: Request):
        """What can be returned from this order (unopened, non-perishable, within the return period), and returns so far."""
        own_order(order_id, payment_id)
        r = upstream("Inventory", lambda: inventory.get(f"/sales/{order_id}/returnable"))
        r.raise_for_status()
        out, lang = r.json(), i18n.language(request)
        out["reason"] = i18n.translate(out.get("reason") or "", lang)
        out["items"] = [{**i, "why_not": i18n.translate(i.get("why_not") or "", lang)} for i in out.get("items", [])]
        return out

    @app.post("/api/orders/{order_id}/payments/{payment_id}/returns", status_code=201)
    def ask_return(order_id: str, payment_id: str, body: ReturnIn):
        own_order(order_id, payment_id)
        if not body.unopened:
            raise HTTPException(422, "Items can only be returned unopened and in the same state as when you got them.")
        if not returns_limiter.allow(order_id):
            raise HTTPException(429, "Too many return requests for this order. Please ask at the store.")
        r = upstream("Inventory", lambda: inventory.post("/returns", json={
            "sale_id": order_id, "lines": [l.model_dump() for l in body.lines], "reason": body.reason.strip(),
            "unopened": True, "started_by": "customer"}))
        if r.status_code in (409, 422):
            d = r.json().get("detail")
            raise HTTPException(r.status_code, d if isinstance(d, str) else "Please check what you chose to return.")
        if r.status_code >= 400:
            log.error("Inventory refused return for %s: %s %s", order_id, r.status_code, r.text[:200])
            raise HTTPException(502, "The return couldn't be recorded. Please try again, or ask at the store.")
        return r.json()

    @app.get("/api/products/{product_id}/image")
    def product_image(product_id: str, request: Request):
        """A product's photo, from the Inventory Service. Versioned addresses (?v=...) are cached by phones for a year."""
        if not PRODUCT_ID.match(product_id):
            raise HTTPException(404, "No photo")
        inm = request.headers.get("if-none-match")
        r = upstream("Inventory", lambda: inventory.get(f"/products/{product_id}/image",
                                                        headers={"If-None-Match": inm} if inm else None))
        cache = "public, max-age=31536000, immutable" if request.query_params.get("v") else "public, max-age=3600"
        if r.status_code == 304:
            return Response(status_code=304, headers={"ETag": r.headers.get("etag", ""), "Cache-Control": cache})
        if r.status_code != 200:
            raise HTTPException(404, "No photo")
        return Response(r.content, media_type="image/jpeg", headers={"ETag": r.headers.get("etag", ""), "Cache-Control": cache})

    @app.get("/api/orders/{order_id}/qr.svg")
    def order_qr(order_id: str):
        """QR code of the order number, scanned by staff when the customer collects."""
        if not ORDER_ID.match(order_id):
            raise HTTPException(404, "Order not found")
        out = io.BytesIO()
        segno.make(order_id, error="m").save(out, kind="svg", scale=6, border=2, dark="#000", light="#fff", xmldecl=False)
        return Response(out.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=86400"})

    def points_for(sale: dict) -> Optional[dict]:
        """For a signed-in customer's order: points spent on it, and earned (or to be earned once paid)."""
        loyalty = accounts.settings() if sale.get("customer_id") else None
        if not loyalty or not loyalty.get("loyalty_enabled"):
            return None
        goods = sum((it.get("unit_price_kes") or 0) * it["qty"] for it in sale["items"])
        off = sale.get("loyalty_discount_kes") or 0
        return {"earn": max(0, goods - off) // loyalty["kes_per_point"], "discount": off,
                "spent": off // loyalty["redeem_kes"] * loyalty["redeem_points"]}

    def sale_lines(sale_id: str) -> Optional[dict]:
        try:
            sale = online_sale(sale_id)
            names = {p["product_id"]: p["name"] for p in products_cache.get()}
        except (HTTPException, httpx.HTTPError):
            return None
        return {"status": sale["status"], "fulfilment": sale.get("fulfilment", "collect"),
                "stage": "cancelled" if sale["status"] == "cancelled" else "collected" if sale.get("exited_at") else "paid"
                if sale["status"] == "paid" else "awaiting_payment", **priced_sale(sale, names)}

    account.install(app, accounts, RateLimiter(10, 900), sale_lines)
    reports.install(app, accounts, "store")
    return app


def _offer_label(discount: Optional[dict]) -> Optional[str]:
    if not discount:
        return None
    return f"{discount['value']}% off" if discount["kind"] == "percent" else f"KES {discount['value']:,} off"


def _same_time(a: Optional[str], b: Optional[str]) -> bool:
    try:
        return bool(a and b) and datetime.fromisoformat(a) == datetime.fromisoformat(b)
    except ValueError:
        return False


def _age_s(record: dict, now: datetime) -> float:
    try:
        created = datetime.fromisoformat(record["created_at"])
    except (KeyError, TypeError, ValueError):
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).total_seconds()
