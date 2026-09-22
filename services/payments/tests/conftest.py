import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.daraja import DarajaClient
from app.main import create_app
from app.store import PaymentStore

SECRET = "test-callback-secret"


@pytest.fixture
def settings():
    return Settings(
        daraja_env="sandbox",
        consumer_key="key",
        consumer_secret="secret",
        shortcode="174379",
        passkey="passkey",
        transaction_type="CustomerPayBillOnline",
        party_b="174379",
        public_base_url="https://example.test",
        callback_secret=SECRET,
        db_path=":memory:",
    )


class FakeDaraja:
    """Records requests and returns canned Daraja responses."""

    def __init__(self):
        self.requests = []
        self.token_calls = 0
        self.stk_response = (200, {
            "MerchantRequestID": "29115-34620561-1",
            "CheckoutRequestID": "ws_CO_191220191020363925",
            "ResponseCode": "0",
            "ResponseDescription": "Success. Request accepted for processing",
            "CustomerMessage": "Success. Request accepted for processing",
        })
        self.query_response = (200, {"ResponseCode": "0", "ResultCode": "0", "ResultDesc": "The service request is processed successfully."})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth/v1/generate":
            self.token_calls += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": "3599"})
        if request.url.path == "/mpesa/stkpush/v1/processrequest":
            code, body = self.stk_response
            return httpx.Response(code, json=body)
        if request.url.path == "/mpesa/stkpushquery/v1/query":
            code, body = self.query_response
            return httpx.Response(code, json=body)
        return httpx.Response(404)

    def last_json(self, path):
        for r in reversed(self.requests):
            if r.url.path == path:
                return json.loads(r.content)
        return None


@pytest.fixture
def fake():
    return FakeDaraja()


@pytest.fixture
def daraja(settings, fake):
    http = httpx.Client(base_url=settings.daraja_base_url, transport=httpx.MockTransport(fake.handler))
    return DarajaClient(settings, http=http)


@pytest.fixture
def paid_events():
    return []


@pytest.fixture
def client(settings, daraja, paid_events):
    app = create_app(settings=settings, daraja=daraja, store=PaymentStore(":memory:"), on_paid=paid_events.append)
    return TestClient(app)


def callback_body(checkout_id, result_code=0, desc="The service request is processed successfully.", receipt="NLJ7RT61SV"):
    cb = {
        "MerchantRequestID": "29115-34620561-1",
        "CheckoutRequestID": checkout_id,
        "ResultCode": result_code,
        "ResultDesc": desc,
    }
    if result_code == 0:
        cb["CallbackMetadata"] = {"Item": [
            {"Name": "Amount", "Value": 100.0},
            {"Name": "MpesaReceiptNumber", "Value": receipt},
            {"Name": "TransactionDate", "Value": 20260923101500},
            {"Name": "PhoneNumber", "Value": 254708374149},
        ]}
    return {"Body": {"stkCallback": cb}}


def decode_password(pw):
    return base64.b64decode(pw).decode()
