"""Payments Service — M-Pesa STK Push via Safaricom Daraja."""

import hmac
import logging
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

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


def create_app(
    settings: Optional[Settings] = None,
    daraja: Optional[DarajaClient] = None,
    store: Optional[PaymentStore] = None,
    on_paid: Optional[Callable[[dict], None]] = None,
) -> FastAPI:
    settings = settings or load_settings()
    daraja = daraja or DarajaClient(settings)
    store = store or PaymentStore(settings.db_path)
    on_paid = on_paid or (lambda payment: log.info("Payment %s paid for sale %s", payment["payment_id"], payment["sale_id"]))

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

    @app.get("/health")
    def health():
        return {"status": "ok", "daraja_env": settings.daraja_env}

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
        _finalise(parsed["checkout_request_id"], parsed["result_code"], parsed["result_desc"], parsed["receipt_number"])
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

