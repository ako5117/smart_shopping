import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.cards import PaystackGateway
from app.main import create_app, payment_ref
from tests.conftest import fresh_store

KEY = "sk_test_abc123"


class FakePaystack:
    """Answers like Paystack's /transaction/initialize and /transaction/verify."""

    def __init__(self):
        self.initialized = []
        self.results = {}  # reference -> verify data
        self.fail_init = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        if request.url.path == "/transaction/initialize":
            if self.fail_init:
                return httpx.Response(400, json={"status": False, "message": "Invalid email"})
            body = json.loads(request.content)
            self.initialized.append(body)
            return httpx.Response(200, json={"status": True, "message": "Authorization URL created", "data": {
                "authorization_url": f"https://checkout.paystack.com/{body['reference'][:8]}",
                "access_code": "ac", "reference": body["reference"]}})
        if request.url.path.startswith("/transaction/verify/"):
            ref = request.url.path.rsplit("/", 1)[1]
            if ref not in self.results:
                return httpx.Response(404, json={"status": False, "message": "Transaction reference not found"})
            return httpx.Response(200, json={"status": True, "message": "Verification successful", "data": self.results[ref]})
        return httpx.Response(404)

    def settle(self, ref, status="success", amount_kes=330, currency="KES"):
        self.results[ref] = {"id": 4099260516, "status": status, "reference": ref, "amount": amount_kes * 100,
                             "currency": currency, "gateway_response": "Approved" if status == "success" else "Declined",
                             "channel": "card", "authorization": {"last4": "4081", "card_type": "visa ", "bank": "TEST BANK"}}


@pytest.fixture
def paystack():
    return FakePaystack()


@pytest.fixture
def card_client(settings, daraja, paid_events, paystack):
    cards = PaystackGateway(KEY, base_url="https://api.paystack.test", http=httpx.Client(
        base_url="https://api.paystack.test", transport=httpx.MockTransport(paystack.handler)))
    return TestClient(create_app(settings=settings, daraja=daraja, store=fresh_store(), on_paid=paid_events.append,
                                 cards=cards))


def checkout(c, **kw):
    body = {"sale_id": "WEB-1", "amount": 330, "email": "jane@example.co.ke",
            "return_url": "https://shop.example.co.ke/store/?order=WEB-1", **kw}
    return c.post("/payments/card/checkout", json=body)


def signed(body: dict, key=KEY):
    raw = json.dumps(body).encode()
    return raw, {"x-paystack-signature": hmac.new(key.encode(), raw, hashlib.sha512).hexdigest(),
                 "content-type": "application/json"}


def test_methods(client, card_client):
    assert client.get("/payments/methods").json() == {"mpesa": True, "card": False, "card_provider": None}
    assert card_client.get("/payments/methods").json()["card"] is True
    assert checkout(client).status_code == 404  # cards not switched on


def test_checkout_sends_the_customer_to_the_hosted_page(card_client, paystack):
    r = checkout(card_client)
    assert r.status_code == 201
    pid = r.json()["payment_id"]
    assert r.json()["checkout_url"] == f"https://checkout.paystack.com/{pid[:8]}"
    assert paystack.initialized == [{"reference": pid, "amount": 33000, "currency": "KES", "email": "jane@example.co.ke",
                                     "callback_url": "https://shop.example.co.ke/store/?order=WEB-1",
                                     "channels": ["card"], "metadata": {"sale_id": "WEB-1"}}]
    p = card_client.get(f"/payments/{pid}").json()
    assert (p["method"], p["status"], p["email"], p["phone_number"]) == ("card", "pending", "jane@example.co.ke", "")


def test_checkout_checks_its_input(card_client, paystack):
    assert checkout(card_client, email="jane").status_code == 422
    assert checkout(card_client, return_url="javascript:alert(1)").status_code == 422
    paystack.fail_init = True
    r = checkout(card_client)
    assert r.status_code == 502 and "Invalid email" in r.json()["detail"]


def test_signed_webhook_settles_the_payment_once(card_client, paystack, paid_events):
    pid = checkout(card_client).json()["payment_id"]
    paystack.settle(pid)
    raw, headers = signed({"event": "charge.success", "data": {"reference": pid, "status": "success"}})
    assert card_client.post("/payments/card/webhook", content=raw, headers=headers).status_code == 200
    assert card_client.post("/payments/card/webhook", content=raw, headers=headers).status_code == 200  # resent
    p = card_client.get(f"/payments/{pid}").json()
    assert (p["status"], p["card_brand"], p["card_last4"], p["provider_ref"]) == ("paid", "Visa", "4081", "4099260516")
    assert [e["payment_id"] for e in paid_events] == [pid]
    assert payment_ref(paid_events[0]) == "CARD-4099260516"


def test_webhook_needs_a_valid_signature_and_is_checked_with_the_provider(card_client, paystack, paid_events):
    pid = checkout(card_client).json()["payment_id"]
    raw, headers = signed({"event": "charge.success", "data": {"reference": pid}}, key="sk_test_wrong")
    assert card_client.post("/payments/card/webhook", content=raw, headers=headers).status_code == 401
    # Correctly signed, but the provider has no such successful charge: the body alone isn't trusted.
    raw, headers = signed({"event": "charge.success", "data": {"reference": pid}})
    card_client.post("/payments/card/webhook", content=raw, headers=headers)
    assert card_client.get(f"/payments/{pid}").json()["status"] == "pending" and paid_events == []
    raw, headers = signed({"event": "charge.success", "data": {"reference": "unknown"}})
    assert card_client.post("/payments/card/webhook", content=raw, headers=headers).status_code == 200


def test_paid_amount_must_match(card_client, paystack, paid_events):
    pid = checkout(card_client).json()["payment_id"]
    paystack.settle(pid, amount_kes=1)
    p = card_client.post(f"/payments/{pid}/refresh").json()
    assert p["status"] == "failed" and "doesn't match" in p["result_desc"] and paid_events == []
    pid = checkout(card_client).json()["payment_id"]
    paystack.settle(pid, currency="NGN")
    assert card_client.post(f"/payments/{pid}/refresh").json()["status"] == "failed"


def test_refresh_when_the_customer_comes_back(card_client, paystack, paid_events):
    pid = checkout(card_client).json()["payment_id"]
    assert card_client.post(f"/payments/{pid}/refresh").json()["status"] == "pending"  # still on the card page
    paystack.settle(pid, status="abandoned")  # left the card page; could still come back and pay
    assert card_client.post(f"/payments/{pid}/refresh").json()["status"] == "pending"
    pid = checkout(card_client).json()["payment_id"]
    paystack.settle(pid, status="failed")
    p = card_client.post(f"/payments/{pid}/refresh").json()
    assert (p["status"], p["result_desc"]) == ("failed", "Declined")
    pid = checkout(card_client).json()["payment_id"]
    paystack.settle(pid)
    assert card_client.post(f"/payments/{pid}/refresh").json()["status"] == "paid"
    assert len(paid_events) == 1


def test_webhook_events_that_dont_settle_a_charge():
    assert PaystackGateway.webhook_reference({"event": "transfer.success", "data": {"reference": "x"}}) is None
    assert PaystackGateway.webhook_reference({"event": "charge.success", "data": {"reference": "x"}}) == "x"
    with pytest.raises(ValueError):
        PaystackGateway("")
