from datetime import datetime, timezone

from dashboard.app import build_overview
from dashboard.sources import read_shelf_events
from tests.conftest import event, write_events

NOW = datetime(2026, 10, 1, 10, 30, tzinfo=timezone.utc)  # 13:30 in Nairobi


def test_overview_combines_inventory_and_shelf(settings, inventory, files):
    write_events(files["events"], [
        event("pick", "A2", "sugar-1kg"),
        event("misplaced", "B1", "sugar-1kg", alert=True, ts="2026-10-01T09:05:00+00:00"),
        event("anomaly", "B1", None, needs_review=True, confidence=0.3, ts="2026-10-01T09:06:00+00:00"),
    ])
    d = build_overview(settings, inventory, now=NOW)

    assert d["errors"] == {}
    assert d["summary"] == {"paid_sales_today": 2, "items_sold_today": 4, "awaiting_payment": 1, "low_stock": 1,
                            "open_discrepancies": 1, "needs_attention": 2, "sync_pending": 2}
    assert [(r["product_id"], r["qty"], r["low"]) for r in d["stock"]] == [("milk-500ml", 20, False),
                                                                             ("sugar-1kg", 3, True)]
    a2, b1 = d["zones"]
    assert a2["products"] == [{"product_id": "sugar-1kg", "name": "Sugar 1 kg", "committed_qty": 3, "low": True}]
    assert a2["last_event"]["event_type"] == "pick" and a2["attention"] == 0
    assert b1["last_event"]["event_type"] == "anomaly" and b1["attention"] == 2
    assert [e["event_type"] for e in d["review"]] == ["anomaly", "misplaced"]  # newest first
    assert d["events"][0]["event_type"] == "anomaly"
    assert d["discrepancies"][0]["name"] == "Sugar 1 kg"
    assert d["sales"][1]["items"][0]["name"] == "Milk 500 ml"


def test_shelf_view_survives_inventory_outage(settings, inventory, fake, files):
    fake.down = True
    write_events(files["events"], [event("pick", "A2", "sugar-1kg")])
    d = build_overview(settings, inventory, now=NOW)

    assert set(d["errors"]) == {"inventory"}
    assert d["stock"] == [] and d["sales"] == [] and d["sync"] is None
    assert d["summary"]["sync_pending"] is None
    assert d["zones"][0]["products"][0] == {"product_id": "sugar-1kg", "name": "Sugar 1 kg",
                                            "committed_qty": None, "low": False}
    assert d["zones"][0]["last_event"]["event_type"] == "pick"


def test_missing_event_log_is_not_an_error(settings, inventory):
    d = build_overview(settings, inventory, now=NOW)
    assert d["errors"] == {} and d["events"] == [] and d["zones"][0]["last_event"] is None


def test_missing_catalogue_is_reported(settings, inventory, files):
    files["config"].unlink()
    d = build_overview(settings, inventory, now=NOW)
    assert set(d["errors"]) == {"shelf_catalogue"}
    assert d["zones"] == [] and len(d["stock"]) == 2  # names still come from the Inventory Service


def test_event_log_tail_skips_bad_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    write_events(path, [event("pick", "A2", "sugar-1kg", qty=i) for i in range(1, 2001)])
    with open(path, "a") as f:
        f.write("not json\n")
        f.write('{"event_type": "pick", "zone')  # partly written line
    events = read_shelf_events(str(path), 50)
    assert [e["qty"] for e in events] == list(range(1953, 2001))  # last 50 lines, two of them unreadable
    assert read_shelf_events(str(tmp_path / "missing.jsonl"), 50) == []


def test_http_endpoints(client, fake):
    assert client.get("/health").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200 and "<title>Store Dashboard</title>" in page.text
    assert client.get("/api/overview").json()["store_id"] == "001"


def test_record_count_goes_to_inventory(client, fake):
    r = client.post("/api/discrepancies/7/count", json={"counted_qty": 4, "counted_by": " Mary "})
    assert r.status_code == 200 and r.json()["stock"] == 4
    assert fake.counts == [{"counted_qty": 4, "counted_by": "Mary"}]

    r = client.post("/api/discrepancies/99/count", json={"counted_qty": 4, "counted_by": "Mary"})
    assert r.status_code == 422 and r.json()["detail"] == "No open discrepancy with that id"
    assert client.post("/api/discrepancies/7/count", json={"counted_qty": -1, "counted_by": "Mary"}).status_code == 422

    fake.down = True
    assert client.post("/api/discrepancies/7/count", json={"counted_qty": 4, "counted_by": "Mary"}).status_code == 502
