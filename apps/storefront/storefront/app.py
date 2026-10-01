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
phone, or a rider delivers (Dispatch Service): the delivery fee comes from the Dispatch Service's area
list, and the customer gives the rider a 4-digit code shown on their order page.
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
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .config import Settings, load_settings
from .shelf import picked_up, read_recent_lines

log = logging.getLogger("storefront")
STATIC = Path(__file__).parent / "static"
ORDER_ID = re.compile(r"^WEB-[0-9A-F]{16}$")
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
    area: str = Field(..., min_length=1, max_length=60)
    address: str = Field(..., min_length=3, max_length=200)
    notes: str = Field("", max_length=300)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)


class OrderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, description="Who collects or receives the order")
    phone: str = Field(..., min_length=9, max_length=16)
    items: List[Line] = Field(..., min_length=1)
    fulfilment: Literal["collect", "delivery"] = "collect"
    delivery: Optional[DeliveryIn] = None


class PayIn(BaseModel):
    phone: str = Field(..., min_length=9, max_length=16)


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
               payments: Optional[httpx.Client] = None, clock=None, dispatch: Optional[httpx.Client] = None) -> FastAPI:
    settings = settings or load_settings()
    inventory = inventory or httpx.Client(base_url=settings.inventory_url, timeout=10.0)
    payments = payments or httpx.Client(base_url=settings.payments_url, timeout=40.0)
    if dispatch is None and settings.dispatch_url:
        dispatch = httpx.Client(base_url=settings.dispatch_url, timeout=10.0)
    clock = clock or (lambda: datetime.now(timezone.utc))
    limiter = RateLimiter(settings.stk_limit_per_phone, settings.stk_limit_window_s)
    app = FastAPI(title="Smart Shopping - Online store")

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
                 "price": p["price_kes"]} for p in r.json() if p.get("price_kes")]

    def fetch_held() -> Dict[str, dict]:
        r = inventory.get(f"/availability/{settings.store_id}")
        r.raise_for_status()
        return r.json()

    def fetch_areas() -> Dict[str, int]:
        r = dispatch.get("/areas")
        r.raise_for_status()
        return {a["area"]: a["fee_kes"] for a in r.json()}

    areas_cache = Cached(fetch_areas, settings.products_cache_s)

    def delivery_areas() -> Optional[Dict[str, int]]:
        """Where riders go, with fees; None when delivery is off or the Dispatch Service is down."""
        if dispatch is None:
            return None
        try:
            return areas_cache.get()
        except httpx.HTTPError as e:
            log.warning("Dispatch unavailable: %s", e)
            return None

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
        return {"status": d["status"], "area": d["area"], "address": d["address"], "notes": d["notes"],
                "fee": d["fee_kes"], "pin": d["pin"] if sale["status"] == "paid" else None, "rider": rider,
                "picked_up_at": d.get("picked_up_at"), "delivered_at": d.get("delivered_at")}

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
        return {"lines": lines, "delivery_fee": fee, "total": sum(l["line_total"] for l in lines) + fee}

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

    def not_enough(short: List[dict], products: List[dict], available: Dict[str, int]):
        by_id = {p["product_id"]: p for p in products}
        detail = [{**s, "name": by_id.get(s["product_id"], {}).get("name", s["product_id"]),
                   "substitutes": substitutes(by_id[s["product_id"]], products, available) if s["product_id"] in by_id else []}
                  for s in short]
        names = ", ".join(d["name"] for d in detail)
        raise HTTPException(409, {"message": f"Not enough in stock: {names}. Change your basket and try again.",
                                  "short": detail})

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
        areas = delivery_areas()
        return {"store_id": settings.store_id, "name": settings.store_name, "currency": "KES",
                "hold_minutes": settings.order_hold_min,
                "delivery": {"available": bool(areas),
                             "areas": [{"area": a, "fee": fee} for a, fee in (areas or {}).items()]}}

    @app.get("/api/catalogue")
    def catalogue():
        products = upstream("Inventory", products_cache.get)
        live = live_availability()
        available = live["available"]
        out = []
        for p in products:
            n = available.get(p["product_id"], 0)
            status = stock_status(n, settings.few_left)
            out.append({**p, "status": status, "left": n if status == "few" else None,
                        "max": min(n, settings.max_qty_per_item),
                        "substitutes": substitutes(p, products, available) if status != "in" else []})
        out.sort(key=lambda p: (p["category"] or "~", p["name"]))
        return {"products": out, "categories": sorted({p["category"] for p in products if p["category"]}),
                "shelf_live": live["shelf_live"], "updated_at": clock().isoformat()}

    @app.post("/api/orders", status_code=201)
    def place_order(body: OrderIn):
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
            areas = delivery_areas()
            if not areas:
                raise HTTPException(503, "Delivery isn't available right now. Choose collect from the store, or try again later.")
            if body.delivery.area not in areas:
                raise HTTPException(422, f"We don't deliver to {body.delivery.area}.")
            fee = areas[body.delivery.area]

        # Fresh numbers for the check, not the few-seconds-old copy the page was showing.
        held_cache.clear()
        shelf_cache.clear()
        available = live_availability()["available"]
        short = [{"product_id": pid, "requested": q, "available": available.get(pid, 0)}
                 for pid, q in merged.items() if q > available.get(pid, 0)]
        if short:
            not_enough(short, products, available)

        sale_id = "WEB-" + secrets.token_hex(8).upper()
        r = upstream("Inventory", lambda: inventory.post("/sales", json={
            "sale_id": sale_id, "store_id": settings.store_id, "channel": "online", "customer_name": body.name.strip(),
            "check_stock": True, "fulfilment": body.fulfilment, "delivery_fee_kes": fee,
            "items": [{"product_id": pid, "qty": q} for pid, q in merged.items()]}))
        if r.status_code == 409 and isinstance(r.json().get("detail"), dict):
            held_cache.clear()
            not_enough(r.json()["detail"]["short"], products, available)
        if r.status_code >= 400:
            log.error("Inventory refused order %s: %s %s", sale_id, r.status_code, r.text[:200])
            raise HTTPException(502, "The store couldn't take your order. Please try again.")
        sale = r.json()
        held_cache.clear()
        if any(not it.get("unit_price_kes") for it in sale["items"]):
            upstream("Inventory", lambda: inventory.post(f"/sales/{sale_id}/cancel"))
            raise HTTPException(409, "Something in your basket isn't sold online any more. Please remove it.")
        total = sum(it["unit_price_kes"] * it["qty"] for it in sale["items"]) + sale.get("delivery_fee_kes", 0)
        if body.fulfilment == "delivery":
            d = body.delivery
            try:
                r = dispatch.post("/deliveries", json={
                    "sale_id": sale_id, "store_id": settings.store_id, "customer_name": body.name.strip(),
                    "customer_phone": body.phone.strip(), "area": d.area, "address": d.address, "notes": d.notes,
                    "lat": d.lat, "lng": d.lng})
                ok = r.status_code == 201
                if not ok:
                    log.error("Dispatch refused delivery %s: %s %s", sale_id, r.status_code, r.text[:200])
            except httpx.HTTPError as e:
                log.error("Dispatch unavailable for %s: %s", sale_id, e)
                ok = False
            if not ok:
                upstream("Inventory", lambda: inventory.post(f"/sales/{sale_id}/cancel"))
                held_cache.clear()
                raise HTTPException(503, "Delivery isn't available right now. Choose collect from the store, or try again later.")
        try:
            payment = start_payment(sale_id, body.phone, total)
        except HTTPException:
            upstream("Inventory", lambda: inventory.post(f"/sales/{sale_id}/cancel"))
            held_cache.clear()
            raise
        return {"order_id": sale_id, "payment_id": payment["payment_id"], "total": total}

    @app.post("/api/orders/{order_id}/pay", status_code=201)
    def pay_again(order_id: str, body: PayIn):
        """Retry after a cancelled or failed M-Pesa prompt, while the order still holds its items."""
        sale = online_sale(order_id)
        if sale["status"] != "pending_payment":
            raise HTTPException(409, "This order is already paid." if sale["status"] == "paid" else "This order was cancelled.")
        if _age_s(sale, clock()) > settings.order_hold_min * 60:
            upstream("Inventory", lambda: inventory.post(f"/sales/{order_id}/cancel"))
            raise HTTPException(409, "This order has expired and its items were released. Please place it again.")
        total = sum((it.get("unit_price_kes") or 0) * it["qty"] for it in sale["items"]) + (sale.get("delivery_fee_kes") or 0)
        payment = start_payment(order_id, body.phone, total)
        return {"order_id": order_id, "payment_id": payment["payment_id"], "total": total}

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
        if payment["status"] == "pending" and _age_s(payment, clock()) >= REFRESH_AFTER_S:
            refreshed = upstream("Payments", lambda: payments.post(f"/payments/{payment_id}/refresh"))
            if refreshed.status_code == 200:
                payment = refreshed.json()
        sale = online_sale(order_id)
        delivery = delivery_for(sale)
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
            "mpesa_receipt": payment.get("mpesa_receipt_number"),
            "phone": mask_phone(payment.get("phone_number", "")),
            "amount": payment.get("amount"),
            "paid_at": payment.get("updated_at") if payment["status"] == "paid" else None,
            "ready_at": sale.get("ready_at"),
            "collected_at": sale.get("exited_at"),
            "result": payment.get("result_desc"),
            **priced_sale(sale, names),
        }

    @app.get("/api/orders/{order_id}/qr.svg")
    def order_qr(order_id: str):
        """QR code of the order number, scanned by staff when the customer collects."""
        if not ORDER_ID.match(order_id):
            raise HTTPException(404, "Order not found")
        out = io.BytesIO()
        segno.make(order_id, error="m").save(out, kind="svg", scale=6, border=2, dark="#000", light="#fff", xmldecl=False)
        return Response(out.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=86400"})

    return app


def _age_s(record: dict, now: datetime) -> float:
    try:
        created = datetime.fromisoformat(record["created_at"])
    except (KeyError, TypeError, ValueError):
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).total_seconds()
