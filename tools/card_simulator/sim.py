"""Stand-in for Paystack's card checkout, for demos and local development without a Paystack account.

    uvicorn sim:app --port 8098        (from tools/card_simulator)

Then run the Payments Service with CARD_PROVIDER=simulator (and CARD_SIM_URL if not on http://localhost:8098).

It imitates the parts of Paystack the Payments Service uses: POST /transaction/initialize, GET
/transaction/verify/{reference}, the hosted payment page, the redirect back, and the signed webhook.
The test cards (Paystack's own test numbers, so nothing changes when switching to a Paystack test key):

    4084 0840 8408 4081   paid
    4000 0000 0000 0002   declined
    any other number      declined ("Invalid card number")
    Cancel                customer leaves without paying

It never moves money, and the page says so: don't type a real card number into it.
"""

import asyncio
import hashlib
import hmac
import html
import json
import os
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

SECRET = os.getenv("CARD_SIM_SECRET", "sk_test_simulator")
PUBLIC_URL = os.getenv("CARD_SIM_PUBLIC_URL", "/card-sim").rstrip("/")  # where browsers reach this app
WEBHOOK_URL = os.getenv("CARD_SIM_WEBHOOK_URL", "")  # Paystack posts to the URL set on its dashboard
PAID_CARD, DECLINED_CARD = "4084084084084081", "4000000000000002"

app = FastAPI(title="Card payment simulator (not Paystack)")
transactions = {}  # reference -> transaction
by_code = {}  # access_code -> reference


def _auth(request: Request):
    if request.headers.get("authorization") != f"Bearer {SECRET}":
        raise HTTPException(401, {"status": False, "message": "Invalid key"})


def _data(t: dict) -> dict:
    return {"id": t["id"], "status": t["status"], "reference": t["reference"], "amount": t["amount"],
            "currency": t["currency"], "channel": "card", "gateway_response": t["gateway_response"],
            "paid_at": t.get("paid_at"), "customer": {"email": t["email"]},
            "authorization": t.get("authorization") or {}}


@app.exception_handler(HTTPException)
async def paystack_errors(request, exc):
    detail = exc.detail if isinstance(exc.detail, dict) else {"status": False, "message": str(exc.detail)}
    return JSONResponse(detail, exc.status_code)


@app.post("/transaction/initialize")
async def initialize(request: Request):
    _auth(request)
    body = await request.json()
    ref = body.get("reference") or secrets.token_hex(8)
    if ref in transactions:
        raise HTTPException(400, {"status": False, "message": "Duplicate Transaction Reference"})
    if not isinstance(body.get("amount"), int) or body["amount"] < 100 or "@" not in str(body.get("email", "")):
        raise HTTPException(400, {"status": False, "message": "Invalid amount or email"})
    code = secrets.token_urlsafe(9)
    transactions[ref] = {"id": secrets.randbelow(10**9) + 4 * 10**9, "reference": ref, "access_code": code,
                         "amount": body["amount"], "currency": body.get("currency", "KES"), "email": body["email"],
                         "callback_url": body.get("callback_url", ""), "status": "abandoned",
                         "gateway_response": "The transaction was not completed"}
    by_code[code] = ref
    return {"status": True, "message": "Authorization URL created",
            "data": {"authorization_url": f"{PUBLIC_URL}/pay/{code}", "access_code": code, "reference": ref}}


@app.get("/transaction/verify/{reference}")
def verify(reference: str, request: Request):
    _auth(request)
    t = transactions.get(reference)
    if not t:
        raise HTTPException(404, {"status": False, "message": "Transaction reference not found"})
    return {"status": True, "message": "Verification successful", "data": _data(t)}


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Card payment (simulator)</title>
<style>
 body {{ margin: 0; font: 16px/1.45 system-ui, sans-serif; background: #f2f4f7; color: #1b1f24; }}
 main {{ max-width: 420px; margin: 24px auto; padding: 0 16px; }}
 .card {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 12px; padding: 18px; }}
 .warn {{ background: #fff4e0; color: #9a5b00; border-radius: 10px; padding: 10px 12px; margin-bottom: 14px; font-size: 14px; }}
 label {{ display: block; font-size: 14px; font-weight: 600; margin: 12px 0 4px; }}
 input {{ font: inherit; width: 100%; box-sizing: border-box; padding: 11px 12px; border: 1px solid #d0d5dd; border-radius: 10px; }}
 .row {{ display: flex; gap: 10px; }} .row > div {{ flex: 1; }}
 button {{ font: inherit; font-weight: 600; width: 100%; margin-top: 16px; padding: 12px; border: 0; border-radius: 10px;
          background: #0b7a5a; color: #fff; cursor: pointer; }}
 button.cancel {{ background: transparent; color: #646d78; margin-top: 8px; }}
 .amount {{ font-size: 28px; font-weight: 800; }} .muted {{ color: #646d78; font-size: 14px; }}
 code {{ background: #eef0f3; padding: 1px 5px; border-radius: 4px; }}
</style></head><body><main>
<div class="warn"><b>Test payment page.</b> This is the Smart Shopping card simulator, not a real payment page.
 Don't type a real card number. Use <code>4084 0840 8408 4081</code> to pay or <code>4000 0000 0000 0002</code> to be declined.</div>
<div class="card">
 <div class="muted">Pay {email}</div>
 <div class="amount">{currency} {amount}</div>
 <form method="post">
  <label for="number">Card number</label>
  <input id="number" name="number" inputmode="numeric" autocomplete="off" placeholder="4084 0840 8408 4081" required>
  <div class="row"><div><label for="exp">Expiry</label><input id="exp" name="exp" placeholder="12/30" autocomplete="off"></div>
   <div><label for="cvv">CVV</label><input id="cvv" name="cvv" placeholder="408" autocomplete="off"></div></div>
  <button>Pay {currency} {amount}</button>
 </form>
 <form method="post"><input type="hidden" name="cancel" value="1"><button class="cancel">Cancel and go back</button></form>
</div></main></body></html>"""


@app.get("/pay/{code}", response_class=HTMLResponse)
def pay_page(code: str):
    t = transactions.get(by_code.get(code, ""))
    if not t:
        raise HTTPException(404, "This payment link has expired.")
    if t["status"] != "abandoned":
        return RedirectResponse(_back(t), 303)
    return PAGE.format(email=html.escape(t["email"]), currency=html.escape(t["currency"]),
                       amount=f"{t['amount'] // 100:,}")


@app.post("/pay/{code}")
async def pay(code: str, number: str = Form(""), cancel: str = Form("")):
    t = transactions.get(by_code.get(code, ""))
    if not t:
        raise HTTPException(404, "This payment link has expired.")
    if t["status"] == "abandoned" and not cancel:
        digits = "".join(c for c in number if c.isdigit())
        if digits == PAID_CARD:
            t.update(status="success", gateway_response="Approved", paid_at=datetime.now(timezone.utc).isoformat(),
                     authorization={"last4": digits[-4:], "card_type": "visa ", "bank": "TEST BANK", "channel": "card"})
        else:
            t.update(status="failed", gateway_response="Declined" if digits == DECLINED_CARD else "Invalid card number",
                     authorization={"last4": digits[-4:], "card_type": "visa ", "bank": "TEST BANK"})
        asyncio.create_task(_webhook(t))
    return RedirectResponse(_back(t), 303)


def _back(t: dict) -> str:
    url = t["callback_url"]
    return f"{url}{'&' if '?' in url else '?'}trxref={t['reference']}&reference={t['reference']}" if url else "/"


async def _webhook(t: dict):
    """Signed like Paystack's: HMAC-SHA512 of the raw body with the secret key, in x-paystack-signature."""
    if not WEBHOOK_URL:
        return
    body = json.dumps({"event": "charge.success" if t["status"] == "success" else "charge.failed",
                       "data": _data(t)}).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(WEBHOOK_URL, content=body, headers={"content-type": "application/json",
                                                                  "x-paystack-signature": sig})
        except httpx.HTTPError:
            pass  # the Payments Service also checks when the customer comes back
