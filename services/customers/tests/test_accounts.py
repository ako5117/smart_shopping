import io
from datetime import timedelta

import pytest
from PIL import Image

from customers.service import Conflict, NotFound, TooMany, WrongCode, normalise_phone
from tests.conftest import JANE, last_code, signed_up


def test_sign_up_confirms_the_email_with_a_code(customers, db):
    assert customers.signup(**JANE) == {"email": "jane@example.com", "sent": True}
    [mail] = customers.emails()
    assert mail["to_email"] == "jane@example.com" and "Confirm your email" in mail["subject"]
    code = last_code(db)
    assert db.one("SELECT code_hash FROM codes")["code_hash"] != code  # only a hash is kept
    with pytest.raises(WrongCode, match="4 tries left"):
        customers.verify("jane@example.com", "000000" if code != "000000" else "111111")
    s = customers.verify("JANE@example.com", code)
    c = s["customer"]
    assert (c["name"], c["phone"], c["email"], c["dob"], c["gender"], c["email_verified"]) == (
        "Jane Wanjiru", "254712345678", "jane@example.com", "1994-05-17", "female", True)
    assert customers.customer_for(s["token"]) == c["customer_id"]
    with pytest.raises(WrongCode, match="expired"):
        customers.verify("jane@example.com", code)  # a code works once


def test_details_are_checked(customers):
    for bad in ({"consent": False}, {"name": " "}, {"phone": "12345"}, {"email": "jane"}, {"dob": "yesterday"},
                {"dob": "2012-01-01"}, {"gender": "robot"}):
        with pytest.raises(ValueError):
            customers.signup(**{**JANE, **bad})
    customers.signup(**{**JANE, "gender": None})  # gender is optional
    assert normalise_phone("+254 712 345678") == normalise_phone("712345678") == "254712345678"


def test_one_account_per_email_and_phone(customers, db):
    signed_up(customers, db)
    with pytest.raises(Conflict, match="Sign in instead"):
        customers.signup(**JANE)
    with pytest.raises(Conflict, match="phone number"):
        customers.signup(**{**JANE, "email": "other@example.com"})
    # An unfinished sign-up doesn't lock a phone number away from the person who confirms
    customers.signup(**{**JANE, "email": "a@example.com", "phone": "0722000000"})
    signed_up(customers, db, email="b@example.com", phone="0722000000")
    assert db.one("SELECT phone FROM customers WHERE email = 'a@example.com'")["phone"] is None


def test_sign_in_with_a_code_and_sign_out(customers, db):
    first = signed_up(customers, db)
    customers.request_code("jane@example.com")
    again = customers.verify("jane@example.com", last_code(db))
    assert again["customer"]["customer_id"] == first["customer"]["customer_id"]
    customers.sign_out(again["token"])
    with pytest.raises(NotFound):
        customers.customer_for(again["token"])
    assert customers.customer_for(first["token"])  # other sessions stay
    # No account: the same answer, and no email
    assert customers.request_code("nobody@example.com") == {"email": "nobody@example.com", "sent": True}
    assert not db.one("SELECT 1 AS x FROM emails WHERE to_email = 'nobody@example.com'")


def test_codes_expire_lock_and_are_limited(customers, db, clock):
    customers.signup(**JANE)
    code = last_code(db)
    clock.now += timedelta(minutes=11)
    with pytest.raises(WrongCode, match="expired"):
        customers.verify("jane@example.com", code)
    customers.request_code("jane@example.com")
    wrong = "999999" if last_code(db) != "999999" else "888888"
    for _ in range(5):
        with pytest.raises(WrongCode):
            customers.verify("jane@example.com", wrong)
    with pytest.raises(WrongCode, match="expired"):
        customers.verify("jane@example.com", last_code(db))  # locked after 5 wrong tries
    customers.request_code("jane@example.com")  # the 3rd in 15 minutes
    with pytest.raises(TooMany):
        customers.request_code("jane@example.com")
    clock.now += timedelta(minutes=16)
    customers.request_code("jane@example.com")


def test_sessions_expire(customers, db, clock):
    s = signed_up(customers, db)
    clock.now += timedelta(days=31)
    with pytest.raises(NotFound):
        customers.customer_for(s["token"])


def test_edit_profile_and_photo(customers, db):
    s = signed_up(customers, db)
    cid = s["customer"]["customer_id"]
    p = customers.update(cid, "Jane W. Kamau", "0799 000 111", "1994-05-17", "")
    assert (p["name"], p["phone"], p["gender"]) == ("Jane W. Kamau", "254799000111", None)
    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), (200, 30, 30)).save(buf, "PNG")
    assert customers.set_photo(cid, buf.getvalue())["photo_version"]
    with Image.open(io.BytesIO(customers.photo(cid))) as img:
        assert (img.format, img.size) == ("JPEG", (400, 400))
    with pytest.raises(ValueError):
        customers.set_photo(cid, b"not a picture")
    assert customers.delete_photo(cid)["photo_version"] is None


def test_delete_account_erases_the_person(customers, db, inventory):
    s = signed_up(customers, db)
    cid = s["customer"]["customer_id"]
    customers.start_order(cid, "WEB-1", 500, False)
    inventory.add("WEB-1")
    customers.settle()
    customers.delete(cid)
    row = db.one("SELECT * FROM customers WHERE customer_id = ?", (cid,))
    assert (row["name"], row["phone"], row["dob"], row["gender"]) == ("", None, None, None)
    assert "jane" not in row["email"] and row["deleted_at"]
    with pytest.raises(NotFound):
        customers.customer_for(s["token"])
    assert customers.balance(cid) == 5  # the points history stays, unnamed
    signed_up(customers, db)  # the email and number are free again


def test_language_for_the_shop_and_the_emails(customers, db):
    customers.signup(**{**JANE, "language": "sw"})
    [mail] = customers.emails()
    assert "Thibitisha barua pepe yako" in mail["subject"] and "Msimbo wako ni" in mail["body"]
    assert "{" not in mail["body"]  # the code and the store name are filled in
    c = customers.verify("jane@example.com", last_code(db))["customer"]
    assert c["language"] == "sw"
    customers.request_code("jane@example.com")  # the account's language
    assert "Msimbo wako wa kuingia" in customers.emails()[0]["subject"]
    customers.request_code("jane@example.com", "en")  # the language the shop is showing now
    assert "Your sign-in code" in customers.emails()[0]["subject"]
    assert customers.set_language(c["customer_id"], "en")["language"] == "en"
    with pytest.raises(ValueError):
        customers.set_language(c["customer_id"], "fr")
    assert customers.signup(**{**JANE, "email": "amos@example.com", "phone": "0722000111", "language": "xx"})["sent"]
    assert "Confirm your email" in customers.emails()[0]["subject"]  # an unknown language: English
