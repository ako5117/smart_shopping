import json

import httpx
import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.sources import InventoryClient

MARY = {"X-Staff-User": "mary", "X-Staff-Role": "staff"}
ADRIAN = {"X-Staff-User": "adrian", "X-Staff-Role": "manager"}


class Returns:
    """The Inventory Service's returns endpoints, in front of the shared fake."""

    def __init__(self, fake):
        self.fake = fake
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path.endswith("/returnable"):
            return httpx.Response(200, json={"open": True, "items": [{"product_id": "sugar-1kg", "returnable": 2}]})
        if path == "/returns" and method == "POST":
            self.calls.append(("start", body, request.headers.get("x-staff-user")))
            return httpx.Response(201, json={"return_id": "RET-1", "total_kes": 360, **body})
        if path == "/returns":
            return httpx.Response(200, json=[{"return_id": "RET-1", "status": s} for s in request.url.params["status"].split(",")])
        if path.startswith("/returns/"):
            action = path.split("/")[3]
            self.calls.append((action, body, None))
            if action == "approve" and not body.get("checked"):
                return httpx.Response(422, json={"detail": "Check the items first: unopened and in the same state as when sold"})
            return httpx.Response(200, json={"return_id": "RET-1", "status": action, "refund_kes": 360})
        return self.fake.handler(request)


@pytest.fixture
def returns_fake(fake):
    return Returns(fake)


@pytest.fixture
def c(settings, returns_fake):
    inventory = InventoryClient("http://inventory.test", http=httpx.Client(
        base_url="http://inventory.test", transport=httpx.MockTransport(returns_fake.handler)))
    return TestClient(create_app(settings, inventory))


def test_page_and_lists(c):
    assert "Returns" in c.get("/returns").text
    assert [r["status"] for r in c.get("/api/returns", headers=MARY).json()] == ["requested", "approved"]
    assert [r["status"] for r in c.get("/api/returns?status=refunded", headers=MARY).json()] == ["refunded"]


def test_staff_take_returns_at_the_counter(c, returns_fake, fake):
    code = fake.sales[0]["sale_id"][-8:]
    found = c.get(f"/api/returns/lookup?code={code}", headers=MARY).json()
    assert found["returnable"]["open"] and found["sale"]["sale_id"].endswith(code)
    r = c.post("/api/returns", json={"sale_id": found["sale"]["sale_id"], "lines": [{"product_id": "sugar-1kg", "qty": 1}],
                                     "reason": "Bought too many", "unopened": True}, headers=MARY)
    assert r.status_code == 201
    assert returns_fake.calls[-1][2] == "mary"  # recorded as started by Mary


def test_only_managers_approve_refuse_and_record_refunds(c, returns_fake):
    for action, body in (("approve", {"checked": True}), ("reject", {"note": "Opened"}), ("refunded", {"method": "cash"})):
        assert c.post(f"/api/returns/RET-1/{action}", json=body, headers=MARY).status_code == 403
    bad = c.post("/api/returns/RET-1/approve", json={"checked": False}, headers=ADRIAN)
    assert bad.status_code == 422 and "Check the items" in bad.json()["detail"]
    assert c.post("/api/returns/RET-1/approve", json={"checked": True}, headers=ADRIAN).json()["status"] == "approve"
    assert returns_fake.calls[-1] == ("approve", {"checked": True, "note": "", "by": "adrian"}, None)
    c.post("/api/returns/RET-1/refunded", json={"method": "mpesa", "ref": "SKR1"}, headers=ADRIAN)
    assert returns_fake.calls[-1][1] == {"method": "mpesa", "ref": "SKR1", "by": "adrian"}
