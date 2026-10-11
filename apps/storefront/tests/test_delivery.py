import httpx
from fastapi.testclient import TestClient

from storefront.app import create_app
from storefront.config import Settings
from tests.conftest import SLOTS

KILIMANI = {"address": "Hse 4, Argwings Kodhek Rd", "notes": "Blue gate", "lat": -1.2921, "lng": 36.7856, "tier": "express"}
THIKA = {**KILIMANI, "lat": -1.0332, "lng": 37.0693}


def deliver(client, items=None, delivery=KILIMANI, phone="0712345678"):
    return client.post("/api/orders", json={"name": "Jane", "phone": phone, "fulfilment": "delivery", "delivery": delivery,
                                            "items": items or [{"product_id": "milk-500ml", "qty": 2}]})


def test_quote_before_shopping(client):
    q = client.get("/api/delivery/quote", params={"lat": -1.2921, "lng": 36.7856}).json()
    assert (q["deliverable"], q["express"]["fee_kes"], q["scheduled"]["fee_kes"]) == (True, 290, 190)
    far = client.get("/api/delivery/quote", params={"lat": -1.0332, "lng": 37.0693}).json()
    assert far["deliverable"] is False and "collected from the store" in far["reason"]
    assert client.get("/api/delivery/quote", params={"lat": 120, "lng": 0}).status_code == 422


def test_express_delivery_charges_the_distance_fee(client, inventory, payments, dispatch):
    r = deliver(client)
    assert r.status_code == 201
    o = r.json()
    assert o["total"] == 2 * 65 + 290
    sale = inventory.sales[o["order_id"]]
    assert (sale["fulfilment"], sale["delivery_fee_kes"]) == ("delivery", 290)
    assert payments.pushes[-1]["amount"] == 420
    d = dispatch.deliveries[o["order_id"]]
    assert (d["customer_name"], d["customer_phone"], d["tier"], d["notes"], d["lat"]) == (
        "Jane", "0712345678", "express", "Blue gate", -1.2921)


def test_scheduled_delivery_in_a_slot(client, inventory, dispatch):
    o = deliver(client, delivery={**KILIMANI, "tier": "scheduled", "slot_start": SLOTS[1]["start"]}).json()
    assert inventory.sales[o["order_id"]]["delivery_fee_kes"] == 190
    assert dispatch.deliveries[o["order_id"]]["slot_start"] == SLOTS[1]["start"]
    r = deliver(client, delivery={**KILIMANI, "tier": "scheduled", "slot_start": "2026-10-01T03:00:00+00:00"})
    assert r.status_code == 422 and "time" in r.json()["detail"]
    assert deliver(client, delivery={**KILIMANI, "tier": "scheduled"}).status_code == 422


def test_the_fee_is_never_taken_from_the_browser(client, inventory):
    o = deliver(client, delivery={**KILIMANI, "fee_kes": 0, "fee": 0}).json()
    assert inventory.sales[o["order_id"]]["delivery_fee_kes"] == 290


def test_too_far_or_express_closed(client, payments, dispatch):
    r = deliver(client, delivery=THIKA)
    assert r.status_code == 422 and "20 km" in r.json()["detail"]
    dispatch.express_open = False
    r = deliver(client)
    assert r.status_code == 422 and "Express" in r.json()["detail"]
    assert payments.pushes == []


def test_delivery_needs_an_address_and_location(client, payments):
    assert deliver(client, delivery={**KILIMANI, "address": ""}).status_code == 422
    assert deliver(client, delivery={k: v for k, v in KILIMANI.items() if k != "lat"}).status_code == 422
    assert deliver(client, delivery={**KILIMANI, "tier": "overnight"}).status_code == 422
    assert client.post("/api/orders", json={"name": "Jane", "phone": "0712345678", "fulfilment": "delivery",
                                            "items": [{"product_id": "milk-500ml", "qty": 1}]}).status_code == 422
    assert payments.pushes == []


def test_dispatch_down_means_no_delivery_and_nothing_held(client, inventory, payments, dispatch):
    dispatch.down = True
    assert client.get("/api/store").json()["delivery"] == {"available": False}
    r = deliver(client)
    assert r.status_code == 503 and "collect" in r.json()["detail"]
    assert inventory.sales == {} and payments.pushes == []


def test_dispatch_refusing_cancels_the_order(client, inventory, payments, dispatch):
    dispatch.refuse = True  # the quote was fine, then dispatch refuses the delivery itself
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
    assert (s["stage"], s["fulfilment"], s["delivery_fee"], s["total"]) == ("packing", "delivery", 290, 420)
    assert s["delivery"]["pin"] == "4821" and s["delivery"]["rider"] is None
    assert (s["delivery"]["tier"], s["delivery"]["slot"], s["delivery"]["distance_km"]) == ("express", "Express", 4.4)

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
    assert r.json()["total"] == 420 and payments.pushes[-1]["amount"] == 420


def test_without_a_dispatch_service_the_shop_is_collect_only(inventory, payments):
    settings = Settings(store_id="001", store_name="Test Store", inventory_url="http://inv", payments_url="http://pay",
                        products_cache_s=0, stock_cache_s=0)
    c = TestClient(create_app(settings,
                              inventory=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(inventory.handler)),
                              payments=httpx.Client(base_url="http://pay", transport=httpx.MockTransport(payments.handler))))
    assert c.get("/api/store").json()["delivery"]["available"] is False
    assert deliver(c).status_code == 503
