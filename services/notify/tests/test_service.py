from datetime import timedelta

import httpx
import pytest

from notify.service import RETRY_AFTER

from .conftest import ago


def bodies(notifier):
    return {m["kind"]: m for m in notifier.messages()}


def test_paid_collect_order_is_texted_once(notifier, stores, sender):
    stores.add_sale()
    assert notifier.watch() == 1
    assert notifier.watch() == 0  # seen already
    notifier.send_due()
    (to, body), = sender.sent
    assert to == "+254712345678"
    assert body == "Mama Mboga Mart: order 0000AAAA is paid, KES 200. We'll text you when it's ready to collect."
    m = notifier.messages()[0]
    assert (m["status"], m["provider_ref"], m["attempts"], m["sale_id"]) == ("sent", "REF-1", 1, "WEB-0000AAAA")
    assert notifier.send_due() == 0


def test_old_orders_are_not_texted(notifier, stores):
    stores.add_sale(paid=ago(hours=3))
    stores.add_sale(sale_id="WEB-2", phone="", paid=ago(minutes=1))  # in-store style order, no phone
    assert notifier.watch() == 0


def test_delivery_paid_text_carries_code_and_fee(notifier, stores):
    stores.add_sale(fulfilment="delivery")
    assert notifier.watch() == 0  # dispatch hasn't got it yet: wait
    stores.deliveries["WEB-0000AAAA"] = {"sale_id": "WEB-0000AAAA", "status": "unassigned", "pin": "0427",
                                         "customer_phone": "0712345678", "rider_id": None}
    assert notifier.watch() == 1
    body = bodies(notifier)["paid"]["body"]
    assert "KES 350" in body and "delivery code is 0427" in body


def test_ready_for_collection(notifier, stores):
    sale = stores.add_sale()
    notifier.watch()
    sale["ready_at"] = ago(seconds=10)
    assert notifier.watch() == 1
    assert "is ready" in bodies(notifier)["ready"]["body"]


def test_ready_not_texted_once_collected_or_for_delivery(notifier, stores):
    stores.add_sale(ready_at=ago(minutes=1), exited_at=ago(seconds=5))
    stores.add_sale(sale_id="WEB-2", fulfilment="delivery", ready_at=ago(minutes=1))
    notifier.watch()
    assert {m["kind"] for m in notifier.messages()} == {"paid"}


def test_on_the_way_names_rider_and_phone(notifier, stores):
    stores.riders["otieno"] = {"rider_id": "otieno", "phone": "0722000111"}
    stores.deliveries["WEB-0000AAAA"] = {"sale_id": "WEB-0000AAAA", "status": "picked_up", "pin": "0427",
                                         "customer_phone": "0712 345 678", "rider_id": "otieno",
                                         "picked_up_at": ago(minutes=2)}
    stores.deliveries["WEB-OLD"] = {"sale_id": "WEB-OLD", "status": "picked_up", "pin": "1111",
                                    "customer_phone": "0712345678", "rider_id": "otieno",
                                    "picked_up_at": ago(hours=5)}
    assert notifier.watch() == 1
    m = bodies(notifier)["on_the_way"]
    assert m["body"] == "Mama Mboga Mart: order 0000AAAA is on the way with otieno (0722000111). Delivery code 0427."
    assert m["to_number"] == "+254712345678"


def test_invalid_number_fails_without_sending(notifier, stores, sender):
    stores.add_sale(phone="12345")
    notifier.watch()
    notifier.send_due()
    m = notifier.messages()[0]
    assert m["status"] == "failed" and "Not a Kenyan mobile" in m["error"] and sender.sent == []
    assert notifier.resend(m["message_id"])["status"] == "failed"


def test_retries_then_gives_up(notifier, stores, sender, clock):
    stores.add_sale()
    notifier.watch()
    sender.outcomes = ["retry"] * 4
    for wait in [timedelta(0), *RETRY_AFTER]:
        clock.now += wait
        assert notifier.send_due() == 0
        assert notifier.send_due() == 0  # not due again until the wait is over
    m = notifier.messages()[0]
    assert (m["status"], m["attempts"]) == ("failed", 4) and len(sender.sent) == 4


def test_retry_then_sent(notifier, stores, sender, clock):
    stores.add_sale()
    notifier.watch()
    sender.outcomes = ["retry"]
    notifier.send_due()
    clock.now += RETRY_AFTER[0]
    assert notifier.send_due() == 1
    m = notifier.messages()[0]
    assert (m["status"], m["attempts"], m["error"]) == ("sent", 2, "")


def test_permanent_failure_and_resend(notifier, stores, sender):
    stores.add_sale()
    notifier.watch()
    sender.outcomes = ["failed"]
    notifier.send_due()
    m = notifier.messages()[0]
    assert m["status"] == "failed"
    again = notifier.resend(m["message_id"])
    assert (again["status"], again["attempts"], again["error"]) == ("queued", 0, "")
    assert notifier.send_due() == 1 and len(sender.sent) == 2
    assert notifier.resend(9999) is None


def test_messages_by_order(notifier, stores):
    stores.add_sale()
    stores.add_sale(sale_id="WEB-2")
    notifier.watch()
    assert [m["sale_id"] for m in notifier.messages(sale_id="WEB-2")] == ["WEB-2"]
    assert len(notifier.messages(limit=1)) == 1


def test_tick_sends_even_when_stores_are_down(notifier, stores, sender):
    stores.add_sale()
    notifier.watch()
    stores.down = True
    notifier.tick()  # logs, doesn't raise
    assert len(sender.sent) == 1


def test_queue_is_idempotent(notifier):
    assert notifier.queue("paid", "S1", "0712345678", "hi") is True
    assert notifier.queue("paid", "S1", "0712345678", "hi") is False
    assert len(notifier.messages()) == 1


def test_without_dispatch_deliveries_wait(db, sender, stores, clock):
    from notify.service import Notifier, Settings
    n = Notifier(db, sender, Settings(), stores.client(), None, clock=clock)
    stores.add_sale(fulfilment="delivery")
    stores.add_sale(sale_id="WEB-2")
    assert n.watch() == 1


def test_inventory_error_raises_from_watch(notifier, stores):
    stores.down = True
    with pytest.raises(httpx.HTTPError):
        notifier.watch()
