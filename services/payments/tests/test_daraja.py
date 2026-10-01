from datetime import datetime, timezone

import pytest

from app.daraja import DarajaError, daraja_timestamp, stk_password
from tests.conftest import decode_password


def test_timestamp_is_converted_to_nairobi_time():
    utc = datetime(2026, 9, 22, 18, 30, 0, tzinfo=timezone.utc)
    assert daraja_timestamp(utc) == "20260922213000"  # UTC+3


def test_password_is_base64_of_shortcode_passkey_timestamp():
    assert decode_password(stk_password("174379", "pk", "20260922213000")) == "174379pk20260922213000"


def test_stk_push_sends_expected_payload(daraja, fake, settings):
    result = daraja.stk_push("254708374149", 100, account_reference="SALE-0001", description="SmartShopping")
    body = fake.last_json("/mpesa/stkpush/v1/processrequest")
    assert body["BusinessShortCode"] == "174379"
    assert body["Amount"] == 100
    assert body["PartyA"] == body["PhoneNumber"] == "254708374149"
    assert body["PartyB"] == "174379"
    assert body["TransactionType"] == "CustomerPayBillOnline"
    assert body["CallBackURL"] == f"https://example.test/payments/mpesa/callback/{settings.callback_secret}"
    assert decode_password(body["Password"]) == f"174379passkey{body['Timestamp']}"
    assert len(body["TransactionDesc"]) <= 13
    assert result.checkout_request_id == "ws_CO_191220191020363925"


def test_access_token_is_cached(daraja, fake):
    daraja.stk_push("254708374149", 10, "A", "B")
    daraja.stk_push("254708374149", 10, "A", "B")
    assert fake.token_calls == 1


def test_http_error_raises_daraja_error(daraja, fake):
    fake.stk_response = (400, {"errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"})
    with pytest.raises(DarajaError, match="Invalid PhoneNumber"):
        daraja.stk_push("254708374149", 10, "A", "B")


def test_rejected_response_code_raises(daraja, fake):
    fake.stk_response = (200, {"ResponseCode": "1", "ResponseDescription": "Rejected"})
    with pytest.raises(DarajaError):
        daraja.stk_push("254708374149", 10, "A", "B")


def test_amount_must_be_positive(daraja):
    with pytest.raises(ValueError):
        daraja.stk_push("254708374149", 0, "A", "B")
