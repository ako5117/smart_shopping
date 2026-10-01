import httpx
from fastapi.testclient import TestClient

from storefront.app import create_app
from storefront.config import Settings

KILIMANI = {"area": "Kilimani", "address": "Hse 4, Argwings Kodhek Rd", "notes": "Blue gate", "lat": -1.2921, "lng": 36.7856}


def deliver(client, items=None, delivery=KILIMANI, phone="0712345678"):
    return client.post("/api/orders", json={"name": "Jane", "phone": phone, "fulfilment": "delivery", "delivery": delivery,
                                            "items": items or [{"product_id": "milk-500ml", "qty": 2}]})


def test_delivery_order_charges_the_area_fee(client, inventory, payments, dispatch):
    r = deliver(client)
    assert r.status_code == 201
    o = r.json()
    assert o["total"] == 2 * 65 + 150
    sale = inventory.sales[o["order_id"]]
    assert (sale["fulfilment"], sale["delivery_fee_kes"]) == ("delivery", 150)
    assert payments.pushes[-1]["amount"] == 280
    d = dispatch.deliveries[o["order_id"]]
    assert (d["customer_name"], d["customer_phone"], d["area"], d["notes"], d["lat"]) == (
        "Jane", "0712345678", "Kilimani", "Blue gate", -1.2921)


def test_delivery_needs_a_known_area_and_address(client, payments):
    assert deliver(client, delivery={**KILIMANI, "area": "Nakuru"}).status_code == 422
    assert deliver(client, delivery={**KILIMANI, "address": ""}).status_code == 422
    assert client.post("/api/orders", json={"name": "Jane", "phone": "0712345678", "fulfilment": "delivery",
                                            "items": [{"product_id": "milk-500ml", "qty": 1}]}).status_code == 422
    assert payments.pushes == []


def test_dispatch_down_means_no_delivery_and_nothing_held(client, inventory, payments, dispatch):
    dispatch.down = True
    assert client.get("/api/store").json()["delivery"] == {"available": False, "areas": []}
    r = deliver(client)
    assert r.status_code == 503 and "collect" in r.json()["detail"]
    assert inventory.sales == {} and payments.pushes == []


def test_dispatch_refusing_cancels_the_order(client, inventory, payments, dispatch):
    client.get("/api/store")  # areas cached... and then dispatch refuses the delivery itself
    dispatch.refuse = True
    assert deliver(client).status_code == 503
    [sale] = inventory.sales.values()
    assert sale["status"] == "cancelled" and payments.pushes == []


def test_delivery_stages_and_the_code_for_the_rider(client, inventory, payments, dispatch):
    o = deliver(client).json()
    url = f"/api/orders/{o['order_id']}/payments/{o['payment_id']}"
    s = client.get(url).json()
    assert s["stage"] == "awaiting_payment" and s["delivery"]["pin"] is None  # no code until paid

    payments.settle(o["payment_id"], "paid", receipt="SJT4K2L9QX")
    d = dispatch.deliveries[o["order_id"]]
    d["status"] = "unassigned"
    s = client.get(url).json()
    assert (s["stage"], s["fulfilment"], s["delivery_fee"], s["total"]) == ("packing", "delivery", 150, 280)
    assert s["delivery"]["pin"] == "4821" and s["delivery"]["rider"] is None

    d.update(status="assigned", rider_id="otieno")
    inventory.sales[o["order_id"]]["ready_at"] = "2026-10-01T12:10:00+00:00"
    s = client.get(url).json()
    assert s["stage"] == "ready" and s["delivery"]["rider"] == {"name": "otieno", "phone": "0722000111"}

    d["status"] = "picked_up"
    inventory.sales[o["order_id"]]["exited_at"] = "2026-10-01T12:15:00+00:00"  # left the store with the rider
    assert client.get(url).json()["stage"] == "on_the_way"
    d["status"] = "failed"
    assert client.get(url).json()["delivery"]["status"] == "failed"
    d["status"] = "delivered"
    assert client.get(url).json()["stage"] == "delivered"


def test_retry_charges_the_fee_again(client, payments):
    o = deliver(client).json()
    payments.settle(o["payment_id"], "cancelled")
    r = client.post(f"/api/orders/{o['order_id']}/pay", json={"phone": "0712345678"})
    assert r.json()["total"] == 280 and payments.pushes[-1]["amount"] == 280


def test_without_a_dispatch_service_the_shop_is_collect_only(inventory, payments):
    settings = Settings(store_id="001", store_name="Test Store", inventory_url="http://inv", payments_url="http://pay",
                        products_cache_s=0, stock_cache_s=0)
    c = TestClient(create_app(settings,
                              inventory=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(inventory.handler)),
                              payments=httpx.Client(base_url="http://pay", transport=httpx.MockTransport(payments.handler))))
    assert c.get("/api/store").json()["delivery"]["available"] is False
    assert deliver(c).status_code == 503
