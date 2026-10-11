import httpx
import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.sources import CustomersClient

MARY = {"X-Staff-User": "mary", "X-Staff-Role": "staff"}
ADRIAN = {"X-Staff-User": "adrian", "X-Staff-Role": "manager"}
SUMMARY = {"members": 2, "points_outstanding": 150, "points_earned": 350, "points_redeemed": 200, "discount_given_kes": 100,
           "loyalty_enabled": True, "kes_per_point": 100, "redeem_points": 100, "redeem_kes": 50}


class FakeCustomers:
    def __init__(self):
        self.down = False
        self.provider = "outbox"

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if path == "/summary":
            return httpx.Response(200, json=SUMMARY)
        if path == "/members":
            return httpx.Response(200, json=[{"customer_id": "C1", "name": "Jane", "email": "j***@example.com",
                                              "created_at": "2026-10-11T08:00:00+00:00", "points": 150, "orders": 3}])
        if path == "/settings":
            return httpx.Response(200, json={"email_provider": self.provider})
        if path == "/emails":
            return httpx.Response(200, json=[{"email_id": 1, "to_email": "jane@example.com", "subject": "Your code",
                                              "body": "Your code is 123456.", "created_at": "2026-10-11T08:00:00+00:00"}])
        return httpx.Response(404)


@pytest.fixture
def fake_customers():
    return FakeCustomers()


@pytest.fixture
def loyalty_client(settings, inventory, fake_customers):
    customers = CustomersClient("http://customers.test", http=httpx.Client(
        base_url="http://customers.test", transport=httpx.MockTransport(fake_customers.handler)))
    return TestClient(create_app(settings, inventory, customers=customers))


def test_managers_see_the_programme_and_members(loyalty_client):
    assert "Loyalty" in loyalty_client.get("/loyalty").text
    body = loyalty_client.get("/api/loyalty", headers=ADRIAN).json()
    assert body["on"] and body["summary"]["members"] == 2
    assert body["members"][0]["email"] == "j***@example.com"
    assert loyalty_client.get("/api/loyalty", headers=MARY).status_code == 403
    assert loyalty_client.get("/api/loyalty/emails", headers=MARY).status_code == 403


def test_stand_in_emails_only_while_email_is_the_outbox(loyalty_client, fake_customers):
    e = loyalty_client.get("/api/loyalty/emails", headers=ADRIAN).json()
    assert e["outbox"] and "123456" in e["emails"][0]["body"]
    fake_customers.provider = "smtp"  # a real email service: codes are never shown to staff
    assert loyalty_client.get("/api/loyalty/emails", headers=ADRIAN).json() == {"on": True, "outbox": False, "emails": []}


def test_customers_service_down_or_not_set_up(loyalty_client, fake_customers, client):
    fake_customers.down = True
    assert "unavailable" in loyalty_client.get("/api/loyalty", headers=ADRIAN).json()["error"]
    assert client.get("/api/loyalty", headers=ADRIAN).json() == {"on": False}
