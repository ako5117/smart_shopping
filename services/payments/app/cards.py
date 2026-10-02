"""Card payments through a hosted checkout page.

The customer types their card details on the provider's own page, never on ours, so card numbers don't
pass through Smart Shopping (PCI DSS SAQ A). The flow:

1. initialize(): the provider gives a checkout URL for this payment; the customer's browser goes there.
2. The customer pays (with 3-D Secure where the card needs it) and is sent back to our return URL.
3. The provider also posts a signed webhook. Either way, verify() asks the provider for the result,
   and the amount and currency are checked against what we asked for before the payment counts.

Paystack is the provider (it takes KES and Kenyan cards). PaystackGateway also talks to
tools/card_simulator, which mimics Paystack's API for demos. Another provider (Pesapal, Flutterwave)
would be another class with the same four methods.
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional

import httpx

PAYSTACK_URL = "https://api.paystack.co"


class CardGatewayError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class CardResult:
    status: str  # paid | failed | cancelled | pending
    amount: Optional[int]  # whole KES
    currency: Optional[str]
    description: str = ""
    brand: Optional[str] = None
    last4: Optional[str] = None
    provider_ref: Optional[str] = None


class PaystackGateway:
    name = "paystack"

    def __init__(self, secret_key: str, base_url: str = PAYSTACK_URL, http: Optional[httpx.Client] = None):
        if not secret_key:
            raise ValueError("A Paystack secret key is needed for card payments")
        self.secret_key = secret_key
        self.http = http or httpx.Client(base_url=base_url, timeout=20.0)
        self.headers = {"Authorization": f"Bearer {secret_key}"}

    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.http.request(method, path, headers=self.headers, **kw)
        except httpx.HTTPError as e:
            raise CardGatewayError(f"Card provider unreachable: {e}")
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or not body.get("status"):
            raise CardGatewayError(body.get("message") or f"Card provider error {r.status_code}", r.status_code)
        return body["data"]

    def initialize(self, reference: str, amount: int, email: str, return_url: str, sale_id: str) -> str:
        """Start a card payment of `amount` KES. Returns the URL to send the customer to."""
        data = self._call("POST", "/transaction/initialize", json={
            "reference": reference, "amount": amount * 100, "currency": "KES", "email": email,
            "callback_url": return_url, "channels": ["card"], "metadata": {"sale_id": sale_id}})
        return data["authorization_url"]

    def verify(self, reference: str) -> CardResult:
        try:
            data = self._call("GET", f"/transaction/verify/{reference}")
        except CardGatewayError as e:
            if e.status_code == 404:
                return CardResult("pending", None, None, "Not paid yet")
            raise
        # "abandoned" means not completed: the customer is still on the card page, or left it. It isn't final,
        # because they can still finish; the shop offers to try again if they come back without paying.
        status = {"success": "paid", "failed": "failed", "reversed": "failed"}.get(data.get("status"), "pending")
        auth = data.get("authorization") or {}
        amount = data.get("amount")
        return CardResult(status=status, amount=amount // 100 if isinstance(amount, int) else None,
                          currency=data.get("currency"), description=data.get("gateway_response") or "",
                          brand=(auth.get("card_type") or "").strip().title() or None, last4=auth.get("last4"),
                          provider_ref=str(data.get("id") or data.get("reference") or ""))

    def signature_ok(self, raw_body: bytes, headers) -> bool:
        """Paystack signs each webhook: HMAC-SHA512 of the raw body with the secret key, in x-paystack-signature."""
        expected = hmac.new(self.secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, headers.get("x-paystack-signature", ""))

    @staticmethod
    def webhook_reference(body: dict) -> Optional[str]:
        """The payment a webhook is about, for the events that settle a charge."""
        if body.get("event") in ("charge.success", "charge.failed"):
            return (body.get("data") or {}).get("reference")
        return None
