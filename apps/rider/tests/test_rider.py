import json

import httpx
import pytest
from fastapi.testclient import TestClient

from rider.app import Settings, create_app, maps_link

ME = {"X-Staff-User": "otieno"}


class FakeDispatch:
    def __init__(self):
        self.riders = {}
        self.calls = []
        self.deliveries = {
            "WEB-000000000000AAAA": {"sale_id": "WEB-000000000000AAAA", "status": "unassigned", "area": "Kilimani",
                                     "address": "Hse 4, Argwings Kodhek Rd", "notes": "Blue gate", "lat": -1.2921,
                                     "lng": 36.7856, "customer_name": "Jane", "customer_phone": "0712345678",
                                     "fee_kes": 150, "rider_id": None, "locked": False, "fail_reason": "",
                                     "created_at": "2026-10-01T12:00:00+00:00"},
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        self.calls.append((method, path, body))
        if path.startswith("/riders/"):
            name = path.split("/")[2]
            if method == "PUT":
                r = self.riders.setdefault(name, {"rider_id": name, "phone": "", "on_shift": False})
                r.update({k: v for k, v in body.items()})
                return httpx.Response(200, json=r)
            return httpx.Response(200, json=self.riders[name]) if name in self.riders else httpx.Response(404, json={"detail": "No rider"})
        if path == "/deliveries":
            statuses = request.url.params["status"].split(",")
            rider = request.url.params.get("rider_id")
            return httpx.Response(200, json=[d for d in self.deliveries.values()
                                             if d["status"] in statuses and (not rider or d["rider_id"] == rider)])
        if path.startswith("/deliveries/"):
            _, _, sale_id, action = path.split("/")
            d = self.deliveries[sale_id]
            if action == "claim":
                if d["status"] != "unassigned":
                    return httpx.Response(409, json={"detail": "Another rider took this job"})
                d.update(status="assigned", rider_id=body["rider_id"])
            elif action == "release":
                d.update(status="unassigned", rider_id=None)
            elif action == "delivered":
                if body["pin"] != "4821":
                    return httpx.Response(422, json={"detail": {"message": "That code isn't right. 4 tries left.", "attempts_left": 4}})
                d["status"] = "delivered"
            elif action == "failed":
                d.update(status="failed", fail_reason=body["reason"])
            return httpx.Response(200, json=d)
        return httpx.Response(404)


def inventory_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"items": [{"product_id": "milk-500ml", "qty": 2}, {"product_id": "sugar-1kg", "qty": 1}]})


@pytest.fixture
def dispatch():
    return FakeDispatch()


@pytest.fixture
def client(dispatch):
    return TestClient(create_app(
        Settings(store_name="Test Store"),
        dispatch=httpx.Client(base_url="http://dispatch", transport=httpx.MockTransport(dispatch.handler)),
        inventory=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(inventory_handler))))


def test_needs_a_rider_login(client):
    assert client.get("/api/me").status_code == 401
    assert "Rider" in client.get("/").text


def test_first_sign_in_registers_the_rider(client, dispatch):
    me = client.get("/api/me", headers=ME).json()
    assert me == {"rider_id": "otieno", "phone": "", "on_shift": False, "store_name": "Test Store"}
    client.put("/api/me", json={"phone": "0722 000 111"}, headers=ME)
    assert dispatch.calls[-1] == ("PUT", "/riders/otieno", {"phone": "0722 000 111"})  # shift left alone


def test_off_shift_riders_see_no_waiting_jobs(client):
    client.get("/api/me", headers=ME)
    assert client.get("/api/jobs", headers=ME).json() == {"mine": [], "available": []}
    client.put("/api/me", json={"on_shift": True}, headers=ME)
    [job] = client.get("/api/jobs", headers=ME).json()["available"]
    # Before accepting: where and how much, not who.
    assert job == {"sale_id": "WEB-000000000000AAAA", "code": "0000AAAA", "area": "Kilimani", "fee": 150,
                   "created_at": "2026-10-01T12:00:00+00:00"}


def test_accept_deliver_with_the_code(client, dispatch):
    client.put("/api/me", json={"on_shift": True}, headers=ME)
    job = client.post("/api/jobs/WEB-000000000000AAAA/accept", headers=ME).json()
    assert (job["customer_name"], job["customer_phone"], job["items"]) == ("Jane", "0712345678", 3)
    assert job["maps"] == "https://www.google.com/maps/dir/?api=1&destination=-1.2921,36.7856"
    assert "pin" not in job
    assert client.post("/api/jobs/WEB-000000000000AAAA/accept", headers=ME).status_code == 409
    [mine] = client.get("/api/jobs", headers=ME).json()["mine"]
    assert mine["status"] == "assigned"

    dispatch.deliveries["WEB-000000000000AAAA"]["status"] = "picked_up"  # staff handed it over
    r = client.post("/api/jobs/WEB-000000000000AAAA/delivered", json={"pin": "1234"}, headers=ME)
    assert r.status_code == 422 and r.json()["detail"] == "That code isn't right. 4 tries left."
    assert client.post("/api/jobs/WEB-000000000000AAAA/delivered", json={"pin": "4821"}, headers=ME).json()["status"] == "delivered"
    assert ("POST", "/deliveries/WEB-000000000000AAAA/delivered", {"rider_id": "otieno", "pin": "4821"}) in dispatch.calls


def test_give_back_and_report_a_problem(client, dispatch):
    client.put("/api/me", json={"on_shift": True}, headers=ME)
    client.post("/api/jobs/WEB-000000000000AAAA/accept", headers=ME)
    client.post("/api/jobs/WEB-000000000000AAAA/release", headers=ME)
    assert dispatch.calls[-1] == ("POST", "/deliveries/WEB-000000000000AAAA/release", {"by": "otieno", "rider_id": "otieno"})
    client.post("/api/jobs/WEB-000000000000AAAA/accept", headers=ME)
    dispatch.deliveries["WEB-000000000000AAAA"]["status"] = "picked_up"
    assert client.post("/api/jobs/WEB-000000000000AAAA/problem", json={"reason": "x"}, headers=ME).status_code == 422
    j = client.post("/api/jobs/WEB-000000000000AAAA/problem", json={"reason": "Customer not answering"}, headers=ME).json()
    assert (j["status"], j["fail_reason"]) == ("failed", "Customer not answering")


def test_maps_link_without_a_pin():
    assert maps_link({"address": "Apt 3B, Rhapta Rd", "area": "Westlands", "lat": None, "lng": None}) == \
        "https://www.google.com/maps/search/?api=1&query=Apt%203B%2C%20Rhapta%20Rd%2C%20Westlands%2C%20Nairobi"


def test_dispatch_down(dispatch):
    def down(request):
        raise httpx.ConnectError("refused", request=request)
    c = TestClient(create_app(Settings(), dispatch=httpx.Client(base_url="http://d", transport=httpx.MockTransport(down)),
                              inventory=httpx.Client(base_url="http://i", transport=httpx.MockTransport(inventory_handler))))
    r = c.get("/api/jobs", headers=ME)
    assert r.status_code == 503 and "connection" in r.json()["detail"]
