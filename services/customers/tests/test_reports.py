import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from customers.api import create_app
from customers.mailer import OutboxMailer
from customers.reports import Reports
from customers.service import NotFound, Settings
from tests.conftest import signed_up


def png(w=2000, h=1500) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def test_report_and_staff_answer(db):
    r = Reports(db)
    rep = r.create("order", "My order was missing the sugar", "store", order_id="web-0123456789abcdef", contact="0712 345 678")
    assert (rep["status"], rep["order_id"], rep["kind_label"], rep["contact"]) == (
        "open", "WEB-0123456789ABCDEF", "A problem with an order", "0712 345 678")
    assert rep["report_id"].startswith("R-") and len(rep["report_id"]) == 12
    with pytest.raises(ValueError, match="what was done"):
        r.update(rep["report_id"], "mary", status="resolved")
    r.update(rep["report_id"], "mary", status="in_progress")
    done = r.update(rep["report_id"], "mary", status="resolved", reply="Sorry! The sugar goes out with today's rider.")
    assert (done["status"], done["handled_by"]) == ("resolved", "mary")
    assert r.public(rep["report_id"]) == {k: done[k] for k in ("report_id", "kind", "kind_label", "status", "reply",
                                                                  "created_at", "updated_at")}
    assert r.list() == [] and [x["report_id"] for x in r.list(["resolved"])] == [rep["report_id"]]
    assert r.counts() == {"open": 0, "in_progress": 0, "resolved": 1}


def test_checks(db):
    r = Reports(db)
    for bad in ({"kind": "rant"}, {"message": "hi"}, {"app": "tv"}, {"order_id": "123"}):
        with pytest.raises(ValueError):
            r.create(**{"kind": "store", "message": "The shelf price is wrong", "app": "store", **bad})
    with pytest.raises(ValueError):
        r.create("app", "The page froze", "store", photo=b"not a picture")
    with pytest.raises(NotFound):
        r.get("R-NOPE")


def test_app_problems_carry_context_and_a_photo(db):
    r = Reports(db)
    rep = r.create("app", "Checkout button does nothing", "scan_and_go",
                   context={"page": "/shop/", "browser": "Mozilla/5.0" * 100, "errors": "TypeError: x is undefined"},
                   photo=png())
    assert rep["has_photo"] and rep["context"]["errors"] == "TypeError: x is undefined"
    assert len(rep["context"]["browser"]) == 500  # trimmed
    with Image.open(io.BytesIO(r.photo(rep["report_id"]))) as img:
        assert (img.format, max(img.size)) == ("JPEG", 1280)


def test_http_signed_in_and_not(db, inventory, clock, customers):
    c = TestClient(create_app(db, inventory, Settings(), OutboxMailer(db), clock, start_worker=False))
    token = signed_up(customers, db)["token"]
    me = {"X-Session": token}
    mine = c.post("/reports", json={"kind": "store", "message": "Bread shelf is empty", "photo_b64": base64.b64encode(png(400, 300)).decode()},
                  headers=me).json()
    anon = c.post("/reports", json={"kind": "idea", "message": "Please stock oat milk", "contact": "a@b.co"}).json()
    assert mine["customer_id"] and anon["customer_id"] is None and mine["has_photo"]
    assert c.post("/reports", json={"kind": "store", "message": "x" * 10, "photo_b64": "%%%"}).status_code == 422
    assert [x["report_id"] for x in c.get("/me/reports", headers=me).json()] == [mine["report_id"]]
    listed = c.get("/reports").json()
    assert listed["counts"]["open"] == 2 and {x["customer_name"] for x in listed["reports"]} == {"Jane Wanjiru", None}
    c.post(f"/reports/{anon['report_id']}", json={"by": "mary", "status": "resolved", "reply": "Coming next week!"})
    assert c.get(f"/reports/{anon['report_id']}/public").json()["reply"] == "Coming next week!"
    assert c.get(f"/reports/{mine['report_id']}/photo").headers["content-type"] == "image/jpeg"
    assert c.get("/reports/R-NOPE/public").status_code == 404


def test_deleting_the_account_clears_the_contact(db, customers):
    s = signed_up(customers, db)
    r = Reports(db).create("order", "Wrong change given", "store", contact="0712345678", customer_id=s["customer"]["customer_id"])
    customers.delete(s["customer"]["customer_id"])
    assert Reports(db).get(r["report_id"])["contact"] == ""
