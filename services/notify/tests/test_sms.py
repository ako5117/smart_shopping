import httpx
import pytest

from notify.sms import SANDBOX_URL, AfricasTalkingSms, SimulatorSms


def at(handler, **kw):
    args = {"username": "store", "api_key": "k3y", **kw}
    return AfricasTalkingSms(http=httpx.Client(base_url="http://at", transport=httpx.MockTransport(handler)), **args)


def answer(code, status="Success"):
    def handler(request):
        return httpx.Response(201, json={"SMSMessageData": {"Message": "Sent to 1/1", "Recipients": [
            {"statusCode": code, "number": "+254712345678", "status": status, "cost": "KES 0.8000",
             "messageId": "ATXid_1"}]}})
    return handler


def test_sends_form_with_key_and_sender_id():
    seen = {}

    def handler(request):
        seen["path"], seen["key"], seen["form"] = request.url.path, request.headers["apiKey"], request.content.decode()
        return answer(101)(request)

    r = at(handler, sender_id="MAMAMBOGA").send("+254712345678", "Hi there")
    assert (r.outcome, r.ref, r.cost) == ("sent", "ATXid_1", "KES 0.8000")
    assert seen["path"] == "/version1/messaging" and seen["key"] == "k3y"
    assert "username=store" in seen["form"] and "from=MAMAMBOGA" in seen["form"]
    assert "to=%2B254712345678" in seen["form"] and "message=Hi+there" in seen["form"]


def test_no_sender_id_means_no_from():
    seen = {}

    def handler(request):
        seen["form"] = request.content.decode()
        return answer(100)(request)

    assert at(handler).send("+254712345678", "x").outcome == "sent"
    assert "from=" not in seen["form"]


@pytest.mark.parametrize("code", [403, 404, 406, 402, 409])
def test_permanent_failures_are_not_retried(code):
    r = at(answer(code, "Rejected")).send("+254712345678", "x")
    assert r.outcome == "failed" and "Rejected" in r.error


@pytest.mark.parametrize("code", [405, 500, 501, 502])
def test_other_failures_are_retried(code):
    assert at(answer(code, "InsufficientBalance")).send("+254712345678", "x").outcome == "retry"


def test_unreachable_is_retried():
    def handler(request):
        raise httpx.ConnectTimeout("slow")
    assert at(handler).send("+254712345678", "x").outcome == "retry"


def test_bad_key_fails():
    r = at(lambda req: httpx.Response(401, text="The supplied authentication is invalid")).send("+254712345678", "x")
    assert r.outcome == "failed" and "API key" in r.error


def test_garbage_answer_is_retried():
    assert at(lambda req: httpx.Response(500, text="<html>oops")).send("+254712345678", "x").outcome == "retry"


def test_sandbox_username_uses_sandbox():
    assert str(AfricasTalkingSms("sandbox", "k").http.base_url).rstrip("/") == SANDBOX_URL


def test_needs_credentials():
    with pytest.raises(ValueError):
        AfricasTalkingSms("", "k")


def test_simulator_always_sends():
    r = SimulatorSms().send("+254712345678", "x")
    assert r.outcome == "sent" and r.ref.startswith("SIM-")
