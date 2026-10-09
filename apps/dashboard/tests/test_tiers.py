"""Standard tier (no shelf sensors) and smart shelves; and stock counts, which both tiers use."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.config import load_settings, store_tier
from tests.conftest import event, write_events

ADRIAN = {"X-Staff-User": "adrian", "X-Staff-Role": "manager"}
MARY = {"X-Staff-User": "mary", "X-Staff-Role": "staff"}


@pytest.fixture
def standard(settings, inventory):
    return TestClient(create_app(replace(settings, store_tier="standard"), inventory))


def test_standard_store_has_no_shelf_panels_and_no_shelf_errors(standard, files):
    files["events"].unlink(missing_ok=True)  # no shelf service at all
    d = standard.get("/api/overview").json()
    assert d["tier"] == "standard" and d["zones"] == [] and d["events"] == [] and d["review"] == []
    assert "shelf_events" not in d["errors"] and "shelf_catalogue" not in d["errors"]
    assert d["stock"] and d["sales"] and d["discrepancies"]  # the rest is all there
    assert standard.get("/api/me").json()["tier"] == "standard"


def test_standard_store_ignores_old_shelf_events(standard, files):
    """Events left behind from when the store had (or demoed) smart shelves don't show."""
    write_events(files["events"], [event("pick", "A2", "sugar-1kg")])
    assert standard.get("/api/overview").json()["events"] == []


def test_standard_store_orders_say_nothing_about_sensor_shelves(standard):
    body = standard.get("/api/orders").json()
    assert body["tier"] == "standard" and all(i["zones"] == [] for o in body["orders"] for i in o["items"])
    assert standard.get("/api/shelf-products").json() == []


def test_smart_store_keeps_its_shelves(client, files):
    write_events(files["events"], [event("pick", "A2", "sugar-1kg")])
    d = client.get("/api/overview").json()
    assert d["tier"] == "smart" and d["zones"] and d["events"]


def test_tier_setting(monkeypatch):
    assert store_tier(" Standard ") == "standard" and store_tier("") == "smart"
    with pytest.raises(ValueError):
        store_tier("premium")
    monkeypatch.setenv("STORE_TIER", "standard")
    assert load_settings().store_tier == "standard"


@pytest.mark.parametrize("tier", ["standard", "smart"])
def test_managers_count_stock_in_either_tier(settings, inventory, fake, tier):
    c = TestClient(create_app(replace(settings, store_tier=tier), inventory))
    count = {"ean13": "6161000000026", "counted_qty": 1, "count_id": "count-0001", "counted_by": "typed"}
    r = c.post("/api/counts", json=count, headers=ADRIAN)
    assert r.status_code == 200 and (r.json()["was"], r.json()["adjusted_by"]) == (3, -2)
    assert fake.stock_counts == [{"ean13": "6161000000026", "counted_qty": 1, "count_id": "count-0001",
                                  "store_id": "001", "counted_by": "adrian"}]
    assert c.get("/api/counts").json()[0]["counted_by"] == "adrian"


def test_count_rules(client, fake):
    count = {"ean13": "6161000000026", "counted_qty": 4, "count_id": "count-0002"}
    r = client.post("/api/counts", json=count, headers=MARY)
    assert r.status_code == 403 and "Only managers" in r.json()["detail"] and fake.stock_counts == []
    assert client.post("/api/counts", json={**count, "ean13": "6161999999999"}, headers=ADRIAN).status_code == 404
    assert client.post("/api/counts", json={**count, "counted_qty": -1}, headers=ADRIAN).status_code == 422
    assert client.post("/api/counts", json=count).status_code == 422  # not signed in, no name typed
    fake.down = True
    assert client.post("/api/counts", json=count, headers=ADRIAN).status_code == 502
    assert client.get("/api/counts").status_code == 502
