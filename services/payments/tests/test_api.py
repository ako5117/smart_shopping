from tests.conftest import SECRET, callback_body


def start_payment(client, phone="0708374149", amount=100, sale_id="SALE-0001"):
    resp = client.post("/payments/mpesa/stk-push", json={"sale_id": sale_id, "phone": phone, "amount": amount})
    assert resp.status_code == 202, resp.text
    return resp.json()["payment_id"]


def test_stk_push_creates_pending_payment(client):
    payment_id = start_payment(client)
    payment = client.get(f"/payments/{payment_id}").json()
    assert payment["status"] == "pending"
    assert payment["phone_number"] == "254708374149"
    assert payment["checkout_request_id"] == "ws_CO_191220191020363925"


def test_invalid_phone_is_rejected(client):
    resp = client.post("/payments/mpesa/stk-push", json={"sale_id": "S1", "phone": "12345", "amount": 100})
    assert resp.status_code == 422


def test_decimal_or_zero_amount_is_rejected(client):
    assert client.post("/payments/mpesa/stk-push", json={"sale_id": "S1", "phone": "0708374149", "amount": 0}).status_code == 422
    assert client.post("/payments/mpesa/stk-push", json={"sale_id": "S1", "phone": "0708374149", "amount": 10.5}).status_code == 422


def test_daraja_failure_marks_payment_failed(client, fake):
    fake.stk_response = (500, {"errorMessage": "Internal error"})
    resp = client.post("/payments/mpesa/stk-push", json={"sale_id": "S1", "phone": "0708374149", "amount": 100})
    assert resp.status_code == 502


def test_successful_callback_marks_paid_and_fires_hook_once(client, paid_events):
    payment_id = start_payment(client)
    body = callback_body("ws_CO_191220191020363925")
    for _ in range(2):  # Daraja may deliver the same callback more than once
        resp = client.post(f"/payments/mpesa/callback/{SECRET}", json=body)
        assert resp.json() == {"ResultCode": 0, "ResultDesc": "Accepted"}
    payment = client.get(f"/payments/{payment_id}").json()
    assert payment["status"] == "paid"
    assert payment["mpesa_receipt_number"] == "NLJ7RT61SV"
    assert len(paid_events) == 1


def test_cancelled_callback(client, paid_events):
    payment_id = start_payment(client)
    client.post(f"/payments/mpesa/callback/{SECRET}", json=callback_body("ws_CO_191220191020363925", 1032, "Request cancelled by user"))
    assert client.get(f"/payments/{payment_id}").json()["status"] == "cancelled"
    assert paid_events == []


def test_final_status_is_not_overwritten(client):
    payment_id = start_payment(client)
    client.post(f"/payments/mpesa/callback/{SECRET}", json=callback_body("ws_CO_191220191020363925", 1032, "Cancelled"))
    client.post(f"/payments/mpesa/callback/{SECRET}", json=callback_body("ws_CO_191220191020363925", 0))
    assert client.get(f"/payments/{payment_id}").json()["status"] == "cancelled"


def test_callback_with_wrong_secret_is_rejected(client):
    start_payment(client)
    resp = client.post("/payments/mpesa/callback/wrong", json=callback_body("ws_CO_191220191020363925"))
    assert resp.status_code == 404


def test_malformed_callback_is_acknowledged(client):
    resp = client.post(f"/payments/mpesa/callback/{SECRET}", json={"unexpected": True})
    assert resp.status_code == 200


def test_refresh_uses_stk_query_when_callback_missing(client, paid_events):
    payment_id = start_payment(client)
    payment = client.post(f"/payments/{payment_id}/refresh").json()
    assert payment["status"] == "paid"
    assert len(paid_events) == 1


def test_refresh_keeps_pending_while_customer_has_not_responded(client, fake):
    payment_id = start_payment(client)
    fake.query_response = (500, {"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"})
    assert client.post(f"/payments/{payment_id}/refresh").json()["status"] == "pending"
