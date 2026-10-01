"""Scan & Go: customers scan products with their phone, pay with M-Pesa, and show the receipt at the exit.

    uvicorn scan_and_go.app:create_app --factory --port 8030

The browser only talks to this app. Prices and totals are always worked out here from the price list,
never taken from the phone. Stock leaves the ledger only when the Payments Service confirms payment
(it commits the sale in the Inventory Service).
"""

import json
import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

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


class CheckoutIn(BaseModel):
    phone: str = Field(..., min_length=9, max_length=16)
    items: List[Line] = Field(..., min_length=1)


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


class Catalogue:
    """Product names and barcodes from the Inventory Service, prices from the price list. Cached for a minute."""

    def __init__(self, inventory: httpx.Client, prices_path: str, ttl_s: int = 60):
        self.inventory, self.prices_path, self.ttl_s = inventory, prices_path, ttl_s
        self._loaded_at = -ttl_s
        self._by_id: Dict[str, dict] = {}
        self._by_ean: Dict[str, dict] = {}
        self.lock = threading.Lock()

    def _load(self) -> None:
        with open(self.prices_path, encoding="utf-8") as f:
            prices = json.load(f)["prices"]
        r = self.inventory.get("/products")
        r.raise_for_status()
        products = [{"product_id": p["product_id"], "ean13": p["ean13"], "name": p["name"],
                     "price": int(prices[p["product_id"]]) if p["product_id"] in prices else None}
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


def create_app(settings: Optional[Settings] = None, inventory: Optional[httpx.Client] = None,
               payments: Optional[httpx.Client] = None) -> FastAPI:
    settings = settings or load_settings()
    inventory = inventory or httpx.Client(base_url=settings.inventory_url, timeout=10.0)
    payments = payments or httpx.Client(base_url=settings.payments_url, timeout=40.0)
    catalogue = Catalogue(inventory, settings.prices_path)
    limiter = RateLimiter(settings.stk_limit_per_phone, settings.stk_limit_window_s)
    app = FastAPI(title="Smart Shopping - Scan & Go")

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

    def priced_sale(sale: dict) -> dict:
        lines = []
        for it in sale["items"]:
            p = catalogue.by_id(it["product_id"]) or {"name": it["product_id"], "price": None}
            lines.append({"product_id": it["product_id"], "name": p["name"], "qty": it["qty"], "price": p["price"],
                          "line_total": (p["price"] or 0) * it["qty"]})
        return {"lines": lines, "total": sum(l["line_total"] for l in lines)}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/store")
    def store():
        return {"store_id": settings.store_id, "name": settings.store_name, "currency": "KES"}

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
    def checkout(body: CheckoutIn):
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
        total = sum(p["price"] * qty for p, qty in lines)

        sale_id = "SG-" + secrets.token_hex(8).upper()
        r = upstream("Inventory", lambda: inventory.post("/sales", json={
            "sale_id": sale_id, "store_id": settings.store_id,
            "items": [{"product_id": p["product_id"], "qty": qty} for p, qty in lines]}))
        if r.status_code >= 400:
            log.error("Inventory refused sale %s: %s %s", sale_id, r.status_code, r.text[:200])
            raise HTTPException(502, "The store system couldn't start your order. Please pay at a till.")

        try:
            payment = start_payment(sale_id, body.phone, total)
        except HTTPException:
            upstream("Inventory", lambda: inventory.post(f"/sales/{sale_id}/cancel"))
            raise
        return {"order_id": sale_id, "payment_id": payment["payment_id"], "total": total}

    @app.post("/api/orders/{order_id}/pay", status_code=201)
    def pay_again(order_id: str, body: PayIn):
        """Retry after a cancelled or failed M-Pesa prompt, for the same order."""
        if not ORDER_ID.match(order_id):
            raise HTTPException(404, "Order not found")
        r = upstream("Inventory", lambda: inventory.get(f"/sales/{order_id}"))
        if r.status_code == 404:
            raise HTTPException(404, "Order not found")
        sale = r.json()
        if sale["status"] != "pending_payment":
            raise HTTPException(409, "This order is already paid." if sale["status"] == "paid" else "This order was cancelled.")
        total = priced_sale(sale)["total"]
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
        if payment["status"] == "pending" and _age_s(payment) >= REFRESH_AFTER_S:
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
            "sale_status": sale["status"],  # pending_payment | paid | cancelled
            "mpesa_receipt": payment.get("mpesa_receipt_number"),
            "phone": mask_phone(payment.get("phone_number", "")),
            "amount": payment.get("amount"),
            "paid_at": payment.get("updated_at") if payment["status"] == "paid" else None,
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
