"""Stand-in for Safaricom Daraja, for demos and local development without a Daraja account.

    uvicorn sim:app --port 8099        (from tools/mpesa_simulator)

Then run the Payments Service with DARAJA_ENV=simulator.

Behaviour, chosen by the last digits of the customer's phone number:
    ...000  customer cancels (result code 1032)
    ...111  insufficient funds (result code 1)
    anything else  paid
The result is posted to the callback URL after SIM_DELAY_S seconds (default 3), as Daraja would.
"""

import asyncio
import os
import secrets
from datetime import datetime

import httpx
from fastapi import FastAPI, Request

app = FastAPI(title="M-Pesa simulator (not Safaricom)")
DELAY_S = float(os.getenv("SIM_DELAY_S", "3"))
results = {}  # CheckoutRequestID -> (code, desc)


def outcome(phone: str):
    if phone.endswith("000"):
        return 1032, "Request cancelled by user"
    if phone.endswith("111"):
        return 1, "The balance is insufficient for the transaction"
    return 0, "The service request is processed successfully."


@app.get("/oauth/v1/generate")
def token():
    return {"access_token": "simulated-" + secrets.token_hex(8), "expires_in": "3599"}


@app.post("/mpesa/stkpush/v1/processrequest")
async def stk_push(request: Request):
    body = await request.json()
    checkout_id = "ws_CO_SIM_" + secrets.token_hex(6)
    merchant_id = "SIM-" + secrets.token_hex(4)
    code, desc = outcome(str(body["PhoneNumber"]))
    asyncio.create_task(_callback(body["CallBackURL"], merchant_id, checkout_id, code, desc,
                                  body["Amount"], body["PhoneNumber"]))
    return {"MerchantRequestID": merchant_id, "CheckoutRequestID": checkout_id, "ResponseCode": "0",
            "ResponseDescription": "Success. Request accepted for processing",
            "CustomerMessage": "Success. Request accepted for processing"}


async def _callback(url, merchant_id, checkout_id, code, desc, amount, phone):
    await asyncio.sleep(DELAY_S)
    results[checkout_id] = (code, desc)
    cb = {"MerchantRequestID": merchant_id, "CheckoutRequestID": checkout_id, "ResultCode": code, "ResultDesc": desc}
    if code == 0:
        cb["CallbackMetadata"] = {"Item": [
            {"Name": "Amount", "Value": amount},
            {"Name": "MpesaReceiptNumber", "Value": "SIM" + secrets.token_hex(4).upper()},
            {"Name": "TransactionDate", "Value": int(datetime.now().strftime("%Y%m%d%H%M%S"))},
            {"Name": "PhoneNumber", "Value": int(phone)}]}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(url, json={"Body": {"stkCallback": cb}})
        except httpx.HTTPError:
            pass  # like Daraja, a failed callback is not retried; the payments service can use /refresh


@app.post("/mpesa/stkpushquery/v1/query")
async def query(request: Request):
    body = await request.json()
    if body["CheckoutRequestID"] not in results:
        from fastapi.responses import JSONResponse
        return JSONResponse({"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}, 500)
    code, desc = results[body["CheckoutRequestID"]]
    return {"ResponseCode": "0", "ResultCode": str(code), "ResultDesc": desc}
