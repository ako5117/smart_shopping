"""Payments Service: M-Pesa STK Push via Safaricom Daraja, and cards through a hosted checkout (app/cards.py)."""

import hmac
import logging
import re
import time
from typing import Callable, Optional

import httpx

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .cards import PAYSTACK_URL, CardGatewayError, PaystackGateway
from .config import Settings, load_settings
from .daraja import DarajaClient, DarajaError
from .phone import normalize_msisdn
from .store import PaymentStore

log = logging.getLogger("payments")

# Daraja result codes with a specific meaning for us; anything else non-zero is a failure.
RESULT_PAID = 0
RESULT_CANCELLED_BY_USER = 1032


class StkPushRequest(BaseModel):
    sale_id: str = Field(..., min_length=1, max_length=64)
    phone: str
    amount: int = Field(..., ge=1, description="Whole KES; Daraja does not accept decimals")


class CardCheckoutRequest(BaseModel):
    sale_id: str = Field(..., min_length=1, max_length=64)
    amount: int = Field(..., ge=1, description="Whole KES")
    email: str = Field(..., max_length=120, description="The card provider sends its receipt here")
    return_url: str = Field(..., max_length=500, description="Where the customer's browser comes back to after paying")


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def payment_ref(payment: dict) -> str:
    """What the Inventory Service records as the payment reference on the sale."""
    if payment.get("method") == "card":
        return "CARD-" + (payment.get("provider_ref") or payment["payment_id"])
    return payment.get("mpesa_receipt_number") or payment.get("checkout_request_id") or payment["payment_id"]


class HideCallbackSecret(logging.Filter):
    """Keep the M-Pesa callback secret out of the access log: anyone with it could post fake results."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "/mpesa/callback/" in args[2]:
            record.args = (*args[:2], re.sub(r"(/mpesa/callback/)[^/?\s]+", r"\1***", args[2]), *args[3:])
        return True


def status_for_result(result_code: int) -> str:
    if result_code == RESULT_PAID:
        return "paid"
    if result_code == RESULT_CANCELLED_BY_USER:
        return "cancelled"
    return "failed"


def parse_callback(body: dict) -> dict:
    cb = body["Body"]["stkCallback"]
    items = {i.get("Name"): i.get("Value") for i in cb.get("CallbackMetadata", {}).get("Item", [])}
    receipt = items.get("MpesaReceiptNumber")
    return {
        "checkout_request_id": cb["CheckoutRequestID"],
        "result_code": int(cb["ResultCode"]),
        "result_desc": cb.get("ResultDesc", ""),
        "receipt_number": str(receipt) if receipt else None,
    }


def inventory_notifier(base_url: str, http: Optional[httpx.Client] = None, attempts: int = 3,
                       backoff_s: float = 1.0) -> Callable[[dict], None]:
    """Tell the Inventory Service a sale is paid, so its stock is committed.

    The commit is idempotent on the inventory side, so retrying is safe. If every attempt fails the
    error is logged loudly: the payment stands, and the sale shows as unpaid in inventory until it is
    committed again (POST /sales/{sale_id}/commit).
    """
    client = http or httpx.Client(base_url=base_url, timeout=10.0)

    def notify(payment: dict) -> None:
        ref = payment_ref(payment)
        for attempt in range(1, attempts + 1):
            try:
                r = client.post(f"/sales/{payment['sale_id']}/commit", json={"payment_ref": ref})
                if r.status_code < 500:
                    if r.status_code >= 400:
                        log.error("Inventory rejected commit for sale %s: %s %s", payment["sale_id"], r.status_code, r.text[:200])
                    return
            except httpx.HTTPError as e:
                log.warning("Inventory commit attempt %d for sale %s failed: %s", attempt, payment["sale_id"], e)
            if attempt < attempts:
                time.sleep(backoff_s * attempt)
        log.error("SALE NOT COMMITTED: sale %s is paid (%s) but inventory could not be reached", payment["sale_id"], ref)

    return notify


def create_app(
    settings: Optional[Settings] = None,
    daraja: Optional[DarajaClient] = None,
    store: Optional[PaymentStore] = None,
    on_paid: Optional[Callable[[dict], None]] = None,
    cards: Optional[PaystackGateway] = None,
) -> FastAPI:
    settings = settings or load_settings()
    daraja = daraja or DarajaClient(settings)
    if cards is None and settings.cards_enabled:
        base = settings.card_sim_url if settings.card_provider == "simulator" else PAYSTACK_URL
        cards = PaystackGateway(settings.paystack_secret_key, base_url=base)
    store = store or PaymentStore(settings.db_path, schema=settings.db_schema)
    if on_paid is None:
        on_paid = (inventory_notifier(settings.inventory_url) if settings.inventory_url
                   else lambda payment: log.info("Payment %s paid for sale %s", payment["payment_id"], payment["sale_id"]))

    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HideCallbackSecret) for f in access_log.filters):
        access_log.addFilter(HideCallbackSecret())

    app = FastAPI(title="Smart Shopping — Payments Service")

    def _finalise(checkout_request_id: str, result_code: int, result_desc: str, receipt: Optional[str]) -> Optional[dict]:
        before = store.get_by_checkout(checkout_request_id)
        if before is None:
            log.warning("Result for unknown CheckoutRequestID %s", checkout_request_id)
            return None
        after = store.apply_result(checkout_request_id, status_for_result(result_code), result_code, result_desc, receipt)
        if after and after["status"] == "paid" and before["status"] != "paid":
            on_paid(after)
        return after

    def _finalise_card(payment: dict) -> dict:
        """Ask the card provider how a card payment ended, and record it once it has."""
        try:
            result = cards.verify(payment["payment_id"])
        except CardGatewayError as e:
            log.warning("Card verify failed for %s: %s", payment["payment_id"], e)
            return payment
        if result.status == "pending":
            return payment
        status, desc = result.status, result.description
        if status == "paid" and (result.amount != payment["amount"] or (result.currency or "KES") != "KES"):
            log.error("CARD AMOUNT MISMATCH for payment %s (sale %s): asked KES %s, provider says %s %s",
                      payment["payment_id"], payment["sale_id"], payment["amount"], result.currency, result.amount)
            status, desc = "failed", "Amount paid doesn't match the order. Contact the store."
        after = store.apply_card_result(payment["payment_id"], status, desc, result.brand, result.last4,
                                        result.provider_ref)
        if after and after["status"] == "paid" and payment["status"] != "paid":
            on_paid(after)
        return after or payment

    @app.get("/health")
    def health():
        return {"status": "ok", "daraja_env": settings.daraja_env}

    @app.get("/payments/methods")
    def methods():
        """Which ways to pay are switched on."""
        return {"mpesa": True, "card": cards is not None,
                "card_provider": settings.card_provider or None}

    @app.post("/payments/card/checkout", status_code=201)
    def card_checkout(req: CardCheckoutRequest):
        """Start a card payment. The customer's browser goes to checkout_url; the result comes back by
        webhook and when they return (GET /payments/{id} after POST /payments/{id}/refresh)."""
        if cards is None:
            raise HTTPException(status_code=404, detail="Card payments aren't switched on")
        email = req.email.strip()
        if not EMAIL.match(email):
            raise HTTPException(status_code=422, detail="Enter a valid email address for the card receipt.")
        if not req.return_url.startswith(("https://", "http://")):
            raise HTTPException(status_code=422, detail="return_url must be an http(s) address")
        payment = store.create(req.sale_id, req.amount, "", method="card", email=email)
        try:
            url = cards.initialize(payment["payment_id"], req.amount, email, req.return_url, req.sale_id)
        except CardGatewayError as e:
            store.mark_failed_to_start(payment["payment_id"], str(e))
            log.error("Card checkout failed for sale %s: %s", req.sale_id, e)
            raise HTTPException(status_code=502, detail=f"Card payment couldn't start: {e}")
        store.mark_card_pending(payment["payment_id"])
        return {"payment_id": payment["payment_id"], "status": "pending", "checkout_url": url}

    @app.post("/payments/card/webhook")
    async def card_webhook(request: Request):
        """The card provider's notice that a charge settled. Signed; the result is then checked with the provider."""
        raw = await request.body()
        if cards is None or not cards.signature_ok(raw, request.headers):
            raise HTTPException(status_code=401, detail="Bad signature")
        try:
            reference = cards.webhook_reference(await request.json())
        except ValueError:
            reference = None
        payment = store.get(reference) if reference else None
        if payment and payment["method"] == "card" and payment["status"] == "pending":
            # Verifying with the provider and telling the Inventory Service can take seconds: off the event loop,
            # so other payments aren't held up meanwhile.
            await run_in_threadpool(_finalise_card, payment)
        return {"received": True}

    @app.post("/payments/mpesa/stk-push", status_code=202)
    def stk_push(req: StkPushRequest):
        try:
            phone = normalize_msisdn(req.phone)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        payment = store.create(req.sale_id, req.amount, phone)
        try:
            result = daraja.stk_push(phone, req.amount, account_reference=req.sale_id, description="SmartShopping")
        except DarajaError as e:
            store.mark_failed_to_start(payment["payment_id"], str(e))
            log.error("STK Push failed for sale %s: %s %s", req.sale_id, e.status_code, e.body)
            raise HTTPException(status_code=502, detail=f"M-Pesa request failed: {e}")

        store.mark_pending(payment["payment_id"], result.merchant_request_id, result.checkout_request_id)
        return {
            "payment_id": payment["payment_id"],
            "status": "pending",
            "customer_message": result.customer_message,
        }

    @app.post("/payments/mpesa/callback/{secret}")
    async def mpesa_callback(secret: str, request: Request):
        if not settings.callback_secret or not hmac.compare_digest(secret, settings.callback_secret):
            raise HTTPException(status_code=404)
        try:
            parsed = parse_callback(await request.json())
        except (KeyError, TypeError, ValueError):
            log.error("Malformed M-Pesa callback")
            # Acknowledge anyway so Daraja does not keep retrying a payload we cannot read.
            return {"ResultCode": 0, "ResultDesc": "Accepted"}
        # Telling the Inventory Service can take seconds (or retry for longer if it's down): off the event loop.
        await run_in_threadpool(_finalise, parsed["checkout_request_id"], parsed["result_code"], parsed["result_desc"],
                                parsed["receipt_number"])
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    @app.get("/payments/{payment_id}")
    def get_payment(payment_id: str):
        payment = store.get(payment_id)
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
        return payment

    @app.post("/payments/{payment_id}/refresh")
    def refresh_payment(payment_id: str):
        """Ask Daraja for the result directly — a fallback when the callback has not arrived."""
        payment = store.get(payment_id)
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment["status"] != "pending":
            return payment
        if payment["method"] == "card":
            return _finalise_card(payment) if cards is not None else payment
        try:
            body = daraja.stk_query(payment["checkout_request_id"])
        except DarajaError as e:
            # Daraja returns an error while the customer has not yet responded; keep it pending.
            log.info("STK query not final for %s: %s", payment_id, e)
            return payment
        if "ResultCode" in body:
            updated = _finalise(payment["checkout_request_id"], int(body["ResultCode"]), body.get("ResultDesc", ""), None)
            return updated or payment
        return payment

    return app

