import json

import httpx
import pytest
from fastapi.testclient import TestClient

from warehouse.api import HttpInventory, Settings, create_app
from warehouse.db import database_from_env
from warehouse.service import Conflict, Unavailable

from .conftest import MILK, SUGAR, fresh_db

ME = {"X-Staff-User": "otieno", "X-Staff-Role": "stock"}


@pytest.fixture
def client(inventory):
    app = create_app(Settings(warehouse_name="Ruaraka Go-down", stores=["001", "002"]), db=fresh_db(), inventory=inventory)
    return TestClient(app)


def post(client, path, body):
    return client.post(path, json=body, headers=ME)


def receive(client, ean=MILK, qty=10, bin="A-01", scan_id="scan-0001"):
    return post(client, "/api/receipts", {"ean13": ean, "qty": qty, "bin": bin, "scan_id": scan_id})


def test_needs_a_login(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "Warehouse" in client.get("/").text  # the page itself loads; it asks for the login's details
    font = client.get("/static/fonts/lexend-400.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"  # served from here, not a font host
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/receipts", json={}).status_code in (401, 422)


def test_dev_user_stands_in_for_the_login(inventory):
    c = TestClient(create_app(Settings(dev_user="dev"), db=fresh_db(), inventory=inventory))
    assert c.get("/api/me").json() == {"name": "dev", "role": "", "warehouse_id": "WH1", "warehouse_name": "Warehouse",
                                       "stores": ["001"]}


def test_me(client):
    assert client.get("/api/me", headers=ME).json() == {
        "name": "otieno", "role": "stock", "warehouse_id": "WH1", "warehouse_name": "Ruaraka Go-down", "stores": ["001", "002"]}


def test_receive_count_move_and_look_up(client):
    r = receive(client).json()
    assert (r["product"]["name"], r["bin_qty"]) == ("Milk 500ml", 10)
    c = post(client, "/api/counts", {"ean13": MILK, "bin": "A-01", "counted_qty": 9, "count_id": "count-0001"}).json()
    assert c["difference"] == -1 and c["counted_by"] == "otieno"
    m = post(client, "/api/moves", {"ean13": MILK, "qty": 4, "from_bin": "A-01", "to_bin": "B-02", "move_id": "move-0001"}).json()
    assert (m["from_qty"], m["to_qty"]) == (5, 4)

    p = client.get(f"/api/products/{MILK}", headers=ME).json()
    assert [(b["bin"], b["qty"]) for b in p["bins"]] == [("A-01", 5), ("B-02", 4)]
    assert [r["bin"] for r in client.get("/api/stock?bin=b-02", headers=ME).json()] == ["B-02"]
    assert [h["entry_type"] for h in client.get("/api/history", headers=ME).json()] == ["move", "move", "count", "receive"]
    assert client.get("/api/counts", headers=ME).json()[0]["counted_qty"] == 9


def test_send_and_receive_at_the_store(client, inventory):
    receive(client)
    body = {"transfer_id": "transfer-0001", "store_id": "002", "lines": [{"ean13": MILK, "bin": "A-01", "qty": 3}]}
    r = post(client, "/api/transfers", body)
    assert r.status_code == 201 and r.json()["created_by"] == "otieno"
    assert post(client, "/api/transfers", body).status_code == 201  # re-sent: same transfer
    assert [t["status"] for t in client.get("/api/transfers?status=dispatched", headers=ME).json()] == ["dispatched"]
    assert post(client, "/api/transfers/transfer-0001/received", {}).json()["status"] == "received"
    assert inventory.store_stock("002", MILK) == 3
    assert client.get("/api/transfers/transfer-0001", headers=ME).json()["received_by"] == "otieno"
    assert client.get("/api/transfers?status=received", headers=ME).json()[0]["transfer_id"] == "transfer-0001"


def test_only_the_stores_this_warehouse_supplies(client):
    receive(client)
    r = post(client, "/api/transfers", {"transfer_id": "transfer-0001", "store_id": "009",
                                        "lines": [{"ean13": MILK, "bin": "A-01", "qty": 1}]})
    assert r.status_code == 422 and "001, 002" in r.json()["detail"]


def test_errors_are_explained(client, inventory):
    assert client.get("/api/products/1234567890123", headers=ME).status_code == 404
    assert client.get("/api/transfers/nope", headers=ME).status_code == 404
    assert receive(client, bin="A 01").status_code == 422  # not a bin code
    receive(client, qty=2)
    r = post(client, "/api/moves", {"ean13": MILK, "qty": 5, "from_bin": "A-01", "to_bin": "B-01", "move_id": "move-0001"})
    assert r.status_code == 409 and r.json()["detail"]["short"][0]["available"] == 2
    post(client, "/api/counts", {"ean13": MILK, "bin": "A-01", "counted_qty": 1, "count_id": "count-0001"})
    r = post(client, "/api/counts", {"ean13": MILK, "bin": "A-01", "counted_qty": 3, "count_id": "count-0001"})
    assert r.status_code == 409 and "already saved" in r.json()["detail"]
    inventory.down = True
    r = receive(client, ean=SUGAR, scan_id="scan-0002")
    assert r.status_code == 503


# ------------------------------------------------------------------ the Inventory Service over HTTP

def inventory_over_http(handler):
    return HttpInventory("http://inv", http=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(handler)))


def test_http_inventory_finds_products_and_restocks():
    sent = []

    def handler(request):
        if request.url.path == "/products":
            return httpx.Response(200, json=[{"product_id": "milk-500ml", "ean13": MILK, "name": "Milk", "price_kes": 60}])
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"created": True})

    inv = inventory_over_http(handler)
    assert inv.product(MILK)["product_id"] == "milk-500ml"
    assert inv.product(SUGAR) is None
    inv.restock("001", MILK, 3, "transfer-T1-milk", "mary")
    assert sent == [{"store_id": "001", "ean13": MILK, "qty": 3, "scan_id": "transfer-T1-milk", "recorded_by": "mary"}]


@pytest.mark.parametrize("response,detail", [(httpx.Response(404, json={"detail": "No product"}), "No product"),
                                             (httpx.Response(422, text="bad request"), "bad request")])
def test_http_inventory_refusals(response, detail):
    inv = inventory_over_http(lambda request: response)
    with pytest.raises(Conflict, match=detail):
        inv.restock("001", MILK, 3, "s", "mary")


def test_http_inventory_down():
    def refused(request):
        raise httpx.ConnectError("refused", request=request)
    with pytest.raises(Unavailable):
        inventory_over_http(refused).product(MILK)
    with pytest.raises(Unavailable):
        inventory_over_http(lambda request: httpx.Response(502)).restock("001", MILK, 1, "s", "")


def test_settings_and_database_from_the_environment(monkeypatch, tmp_path):
    from warehouse.api import load_settings
    monkeypatch.setenv("WAREHOUSE_ID", "WH9")
    monkeypatch.setenv("WAREHOUSE_STORES", "001, 002,")
    s = load_settings()
    assert (s.warehouse_id, s.stores) == ("WH9", ["001", "002"])
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("WAREHOUSE_DB_PATH", str(tmp_path / "wh.db"))
    assert database_from_env().url.endswith("wh.db")
