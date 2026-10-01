"""Minimal client for Safaricom Daraja M-Pesa Express (STK Push)."""

import base64
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from .config import Settings

NAIROBI = ZoneInfo("Africa/Nairobi")
TOKEN_REFRESH_MARGIN_S = 300  # refresh 5 minutes before Daraja's 1-hour expiry


class DarajaError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, body: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


@dataclass
class StkPushResult:
    merchant_request_id: str
    checkout_request_id: str
    response_code: str
    response_description: str
    customer_message: str


def daraja_timestamp(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(NAIROBI)
    return now.astimezone(NAIROBI).strftime("%Y%m%d%H%M%S")


def stk_password(shortcode: str, passkey: str, timestamp: str) -> str:
    return base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()


class DarajaClient:
    def __init__(self, settings: Settings, http: Optional[httpx.Client] = None):
        self.settings = settings
        self.http = http or httpx.Client(base_url=settings.daraja_base_url, timeout=30.0)
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        resp = self.http.get(
            "/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(self.settings.consumer_key, self.settings.consumer_secret),
        )
        if resp.status_code != 200:
            raise DarajaError("Failed to get Daraja access token", resp.status_code, _safe_json(resp))
        data = resp.json()
        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 3599))
        self._token_expires_at = time.monotonic() + max(expires_in - TOKEN_REFRESH_MARGIN_S, 60)
        return self._token

    def _post(self, path: str, payload: dict) -> dict:
        resp = self.http.post(
            path,
            json=payload,
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        body = _safe_json(resp)
        if resp.status_code != 200:
            message = body.get("errorMessage") or body.get("ResponseDescription") or "Daraja request failed"
            raise DarajaError(message, resp.status_code, body)
        return body

    def stk_push(self, phone: str, amount: int, account_reference: str, description: str) -> StkPushResult:
        if amount < 1:
            raise ValueError("Amount must be at least 1 KES")
        ts = daraja_timestamp()
        s = self.settings
        payload = {
            "BusinessShortCode": s.shortcode,
            "Password": stk_password(s.shortcode, s.passkey, ts),
            "Timestamp": ts,
            "TransactionType": s.transaction_type,
            "Amount": amount,
            "PartyA": phone,
            "PartyB": s.party_b,
            "PhoneNumber": phone,
            "CallBackURL": s.callback_url,
            "AccountReference": account_reference[:12],
            "TransactionDesc": description[:13],
        }
        body = self._post("/mpesa/stkpush/v1/processrequest", payload)
        if str(body.get("ResponseCode")) != "0":
            raise DarajaError(body.get("ResponseDescription", "STK Push rejected"), 200, body)
        return StkPushResult(
            merchant_request_id=body["MerchantRequestID"],
            checkout_request_id=body["CheckoutRequestID"],
            response_code=str(body["ResponseCode"]),
            response_description=body.get("ResponseDescription", ""),
            customer_message=body.get("CustomerMessage", ""),
        )

    def stk_query(self, checkout_request_id: str) -> dict:
        ts = daraja_timestamp()
        s = self.settings
        payload = {
            "BusinessShortCode": s.shortcode,
            "Password": stk_password(s.shortcode, s.passkey, ts),
            "Timestamp": ts,
            "CheckoutRequestID": checkout_request_id,
        }
        return self._post("/mpesa/stkpushquery/v1/query", payload)


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {"raw": resp.text[:500]}
