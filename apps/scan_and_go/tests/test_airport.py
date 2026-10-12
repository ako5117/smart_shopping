import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scan_and_go.airport import Airport, FlightBoard, NotAllowed, date_from_day, normalise_flight, parse_bcbp

NAIROBI = ZoneInfo("Africa/Nairobi")
NOW = datetime(2026, 10, 12, 18, 0, tzinfo=NAIROBI)  # day 285


def bcbp(name="WANJIRU/JANE MS", pnr="ABC123", frm="NBO", to="LHR", carrier="KQ", number="0100", day=285, cabin="Y",
         seat="023A", extra_legs=()):
    """A boarding pass string in IATA BCBP format M (mandatory fields only)."""
    def leg(pnr, frm, to, carrier, number, day, cabin, seat, cond=""):
        return (f"{pnr:<7}{frm}{to}{carrier:<3}{number:<5}{day:03d}{cabin}{seat:>4}{'0045':<5}1{len(cond):02X}{cond}")
    legs = [leg(pnr, frm, to, carrier, number, day, cabin, seat, cond=">5180")] + [leg(*l) for l in extra_legs]
    return f"M{len(legs)}{name:<20}E" + "".join(legs)


def airport(flights=None, tmp_path=None, now=NOW):
    board = FlightBoard("")
    if flights is not None:
        path = tmp_path / "flights.json"
        path.write_text(json.dumps(flights))
        board = FlightBoard(str(path))
    return Airport("NBO", board, clock=lambda: now)


def test_reads_a_boarding_pass_but_not_the_booking_reference():
    [leg] = parse_bcbp(bcbp())
    assert (leg.name, leg.from_airport, leg.to_airport, leg.flight, leg.day_of_year, leg.cabin, leg.seat) == (
        "Jane Wanjiru", "NBO", "LHR", "KQ100", 285, "Y", "23A")
    assert "ABC123" not in repr(leg)  # never kept
    with pytest.raises(ValueError):
        parse_bcbp("6161000000040")  # a product barcode
    with pytest.raises(ValueError):
        parse_bcbp(bcbp()[:50])


def test_connecting_flights_use_the_leg_from_this_airport():
    p = bcbp(frm="MBA", to="NBO", carrier="KQ", number="0603", extra_legs=[("XYZ789", "NBO", "AMS", "KL", "0566", 285, "J", "002C")])
    assert [l.flight for l in parse_bcbp(p)] == ["KQ603", "KL566"]
    t = airport().from_boarding_pass(p)
    assert (t["flight"], t["from"], t["to"], t["seat"], t["cabin"]) == ("KL566", "NBO", "AMS", "2C", "J")


def test_traveller_record():
    t = airport().from_boarding_pass(bcbp())
    assert t == {"name": "Jane Wanjiru", "flight": "KQ100", "from": "NBO", "to": "LHR", "date": "2026-10-12", "seat": "23A",
                 "cabin": "Y", "departs": "", "gate": "", "source": "boarding_pass"}


def test_wrong_airport_or_day():
    with pytest.raises(NotAllowed, match="from MBA, not NBO"):
        airport().from_boarding_pass(bcbp(frm="MBA", to="KIS"))
    with pytest.raises(NotAllowed, match="02 Oct"):
        airport().from_boarding_pass(bcbp(day=275))
    assert airport().from_boarding_pass(bcbp(day=286))["date"] == "2026-10-13"  # just after midnight flights
    with pytest.raises(NotAllowed, match="not today"):
        airport().from_boarding_pass(bcbp(day=290))


def test_flight_board(tmp_path):
    board = [{"flight": "KQ 100", "to": "LHR", "departs": "23:55", "gate": "14"},
             {"flight": "ET309", "to": "ADD", "departs": "17:30", "gate": "3"}]
    t = airport(board, tmp_path).from_boarding_pass(bcbp())
    assert (t["departs"], t["gate"]) == ("23:55", "14")
    with pytest.raises(NotAllowed, match="can't find KQ200"):
        airport(board, tmp_path).from_boarding_pass(bcbp(number="0200"))
    with pytest.raises(NotAllowed, match="due to leave at 17:30"):
        airport(board, tmp_path).from_typed("Abebe Bikila", "ET 309")
    with pytest.raises(NotAllowed, match="goes to LHR, not JFK"):
        airport(board, tmp_path).from_typed("Jane Wanjiru", "kq100", "JFK")


def test_typed_flight_details(tmp_path):
    t = airport().from_typed("  Jane   Wanjiru ", "kq-100", "lhr")
    assert (t["name"], t["flight"], t["to"], t["date"], t["source"]) == ("Jane Wanjiru", "KQ100", "LHR", "2026-10-12", "typed")
    for bad in (("", "KQ100"), ("Jane", "100"), ("Jane", "KQ100", "LONDON")):
        with pytest.raises(ValueError):
            airport().from_typed(*bad)


def test_helpers():
    assert normalise_flight("ET 0309") == "ET309" and normalise_flight("5Z 12A") == "5Z12A"
    assert date_from_day(1, date(2026, 12, 30)) == date(2027, 1, 1)
    assert date_from_day(365, date(2027, 1, 2)) == date(2026, 12, 31)
    with pytest.raises(ValueError):
        Airport("Nairobi", FlightBoard(""))


# --- The app in airport mode

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from scan_and_go.app import create_app  # noqa: E402
from scan_and_go.config import Settings  # noqa: E402


@pytest.fixture
def flyers(inventory, payments, tmp_path):
    board = tmp_path / "flights.json"
    board.write_text(json.dumps([{"flight": "KQ 100", "to": "LHR", "departs": "23:59", "gate": "14"}]))
    settings = Settings(store_id="001", store_name="JKIA Duty Free", inventory_url="http://inv", payments_url="http://pay",
                        store_mode="airport", airport_code="NBO", flights_file=str(board))
    return TestClient(create_app(
        settings,
        inventory=httpx.Client(base_url="http://inv", transport=httpx.MockTransport(inventory.handler)),
        payments=httpx.Client(base_url="http://pay", transport=httpx.MockTransport(payments.handler)),
    ))


def today_pass(**kw):
    return bcbp(day=datetime.now(NAIROBI).timetuple().tm_yday, **kw)


def test_store_says_airport(flyers, client):
    s = flyers.get("/api/store").json()
    assert (s["mode"], s["airport"], s["tz"]) == ("airport", "NBO", "Africa/Nairobi")
    assert (client.get("/api/store").json()["mode"], client.post("/api/traveller", json={}).status_code) == ("shop", 404)


def test_check_a_boarding_pass(flyers):
    if datetime.now(NAIROBI).strftime("%H:%M") >= "23:59":
        pytest.skip("the demo flight has left")
    t = flyers.post("/api/traveller", json={"boarding_pass": today_pass()}).json()
    assert (t["name"], t["flight"], t["to"], t["gate"]) == ("Jane Wanjiru", "KQ100", "LHR", "14")
    assert flyers.post("/api/traveller", json={"boarding_pass": today_pass(number="0200")}).status_code == 409
    assert flyers.post("/api/traveller", json={"boarding_pass": "6161000000040"}).status_code == 422
    assert flyers.post("/api/traveller", json={"name": "Jane Wanjiru", "flight": "KQ 100"}).json()["to"] == "LHR"


def test_checkout_needs_and_records_the_traveller(flyers, inventory, payments):
    if datetime.now(NAIROBI).strftime("%H:%M") >= "23:59":
        pytest.skip("the demo flight has left")
    basket = {"phone": "0712345678", "items": [{"product_id": "milk-500ml", "qty": 1}]}
    assert flyers.post("/api/checkout", json=basket).status_code == 422
    assert flyers.post("/api/checkout", json={**basket, "traveller": {"name": "Jane", "flight": "KQ 200"}}).status_code == 409
    assert inventory.sales == {}
    r = flyers.post("/api/checkout", json={**basket, "traveller": {"boarding_pass": today_pass()}})
    assert r.status_code in (201, 202), r.text
    [sale] = inventory.sales.values()
    assert sale["traveller"]["flight"] == "KQ100" and sale["customer_name"] == "Jane Wanjiru"
    assert "ABC123" not in json.dumps(sale)  # the booking reference never leaves the phone's barcode
