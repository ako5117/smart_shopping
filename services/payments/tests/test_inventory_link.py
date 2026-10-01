import httpx

from app.main import inventory_notifier

PAYMENT = {"payment_id": "p1", "sale_id": "SALE-1", "mpesa_receipt_number": "NLJ7RT61SV", "checkout_request_id": "ws_1"}


def recorder(statuses):
    calls = []

    def handler(request):
        calls.append((request.url.path, request.content))
        code = statuses.pop(0) if statuses else 200
        if code == "down":
            raise httpx.ConnectError("unreachable")
        return httpx.Response(code, json={})

    return calls, httpx.Client(base_url="http://inventory", transport=httpx.MockTransport(handler))


def test_paid_sale_is_committed_with_the_mpesa_receipt():
    calls, http = recorder([200])
    inventory_notifier("http://inventory", http=http)(PAYMENT)
    assert calls == [("/sales/SALE-1/commit", b'{"payment_ref":"NLJ7RT61SV"}')]


def test_retries_when_inventory_is_down_then_succeeds():
    calls, http = recorder(["down", 503, 200])
    inventory_notifier("http://inventory", http=http, backoff_s=0)(PAYMENT)
    assert len(calls) == 3


def test_gives_up_after_three_attempts_and_logs(caplog):
    calls, http = recorder(["down", "down", "down"])
    inventory_notifier("http://inventory", http=http, backoff_s=0)(PAYMENT)
    assert len(calls) == 3 and "SALE NOT COMMITTED" in caplog.text


def test_client_errors_are_not_retried(caplog):
    calls, http = recorder([409])
    inventory_notifier("http://inventory", http=http, backoff_s=0)(PAYMENT)
    assert len(calls) == 1 and "rejected" in caplog.text
