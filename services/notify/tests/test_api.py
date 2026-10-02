import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from notify import api
from notify.service import Settings
from notify.sms import AfricasTalkingSms, SimulatorSms


@pytest.fixture
def client(db, sender, stores, notifier):
    app = api.create_app(db=db, sender=sender, inventory=stores.client(), dispatch=stores.client(),
                         start_worker=False)
    app.state.notifier.clock = notifier.clock
    app.state.notifier.settings = Settings("001", "Mama Mboga Mart")
    with TestClient(app) as c:
        yield c, app.state.notifier


def test_health_and_messages(client, stores):
    c, n = client
    assert c.get("/health").json() == {"status": "ok", "provider": "fake"}
    stores.add_sale()
    n.tick()
    rows = c.get("/messages").json()
    assert len(rows) == 1 and rows[0]["status"] == "sent"
    assert c.get("/messages", params={"sale_id": "nope"}).json() == []


def test_resend(client, stores):
    c, n = client
    stores.add_sale()
    stores.add_sale(sale_id="WEB-2", phone="999")
    n.tick()
    by_sale = {m["sale_id"]: m for m in c.get("/messages").json()}
    r = c.post(f"/messages/{by_sale['WEB-0000AAAA']['message_id']}/resend")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert c.post(f"/messages/{by_sale['WEB-2']['message_id']}/resend").status_code == 409
    assert c.post("/messages/9999/resend").status_code == 404


def test_texts_off(db, stores, monkeypatch):
    monkeypatch.delenv("SMS_PROVIDER", raising=False)
    app = api.create_app(db=db, inventory=stores.client(), start_worker=False)
    with TestClient(app) as c:
        assert c.get("/health").json()["provider"] is None
        assert c.get("/messages").json() == []
        assert c.post("/messages/1/resend").status_code == 503


def test_worker_thread_runs(db, stores, monkeypatch):
    monkeypatch.setenv("NOTIFY_POLL_S", "0.05")
    stores.add_sale(paid=datetime.now(timezone.utc).isoformat())
    app = api.create_app(db=db, sender=SimulatorSms(), inventory=stores.client(), dispatch=stores.client())
    with TestClient(app) as c:
        for _ in range(100):
            rows = c.get("/messages").json()
            if rows and rows[0]["status"] == "sent":
                break
            time.sleep(0.05)
        assert rows[0]["status"] == "sent" and rows[0]["provider_ref"].startswith("SIM-")


def test_sender_from_env(monkeypatch):
    monkeypatch.setenv("SMS_PROVIDER", "simulator")
    assert isinstance(api.sender_from_env(), SimulatorSms)
    monkeypatch.setenv("SMS_PROVIDER", "AfricasTalking")
    monkeypatch.setenv("AT_USERNAME", "sandbox")
    monkeypatch.setenv("AT_API_KEY", "k")
    monkeypatch.setenv("AT_SENDER_ID", "SHOP")
    s = api.sender_from_env()
    assert isinstance(s, AfricasTalkingSms) and s.sender_id == "SHOP"
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    with pytest.raises(ValueError):
        api.sender_from_env()
    monkeypatch.setenv("SMS_PROVIDER", "")
    assert api.sender_from_env() is None
