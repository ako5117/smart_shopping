from fastapi.testclient import TestClient

from customers.api import create_app
from customers.mailer import OutboxMailer
from customers.service import Settings
from tests.conftest import JANE, last_code


def app(db, inventory, clock, **kw):
    return TestClient(create_app(db, inventory, Settings(**kw), OutboxMailer(db), clock, start_worker=False))


def test_sign_up_sign_in_and_points_over_http(db, inventory, clock):
    c = app(db, inventory, clock)
    assert c.get("/settings").json()["email_provider"] == "outbox"
    assert c.post("/signup", json={**JANE, "consent": False}).status_code == 422
    assert c.post("/signup", json=JANE).status_code == 202
    bad = c.post("/sessions", json={"email": "jane@example.com", "code": "x"})
    assert bad.status_code == 422 and bad.json()["detail"]["attempts_left"] == 4
    s = c.post("/sessions", json={"email": "jane@example.com", "code": last_code(db)}).json()
    me = {"X-Session": s["token"]}
    assert c.get("/me").status_code == 404
    assert c.get("/me", headers=me).json()["loyalty"]["points"] == 0
    assert c.post("/orders", json={"sale_id": "WEB-1", "goods_kes": 1500, "use_points": False}, headers=me).status_code == 201
    inventory.add("WEB-1", items=((1500, 1),))
    assert c.get("/me", headers=me).json()["loyalty"]["points"] == 15  # settled when the customer looks
    assert c.get("/members").json()[0]["points"] == 15
    assert c.delete("/sessions", headers=me).status_code == 204
    assert c.get("/me", headers=me).status_code == 404


def test_loyalty_off_from_settings(db, inventory, clock):
    c = app(db, inventory, clock, loyalty_enabled=False)
    assert c.get("/settings").json()["loyalty_enabled"] is False


def test_language_over_http(db, inventory, clock):
    c = app(db, inventory, clock)
    assert c.post("/signup", json={**JANE, "language": "sw"}).status_code == 202
    s = c.post("/sessions", json={"email": "jane@example.com", "code": last_code(db)}).json()
    me = {"X-Session": s["token"]}
    assert c.get("/me", headers=me).json()["language"] == "sw"
    assert c.put("/me/language", json={"language": "en"}, headers=me).json()["language"] == "en"
    assert c.put("/me/language", json={"language": "fr"}, headers=me).status_code == 422
    assert c.post("/codes", json={"email": "jane@example.com", "language": "sw"}).status_code == 202
