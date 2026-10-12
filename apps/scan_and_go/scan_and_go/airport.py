"""Airport mode (STORE_MODE=airport): travellers shop without an account. They scan their boarding pass, or type
their flight, before paying, and the sale records who flew where (as airport shops need for duty-free and
customs records).

Boarding passes carry an IATA "Bar Coded Boarding Pass" (BCBP) string in their PDF417, Aztec or QR code:

    M1WANJIRU/JANE MS      EABC123 NBOLHRKQ 0100 285Y023A0045 100
    | |                    ||      |  |  |  |    |  ||   |    | +-- size of the optional fields that follow (hex)
    | name (20)            |PNR(7) from to airline flight day-of-year, cabin, seat, check-in no., status
    format, number of legs e-ticket

Only the mandatory fields are read. The booking reference (PNR) is read past but never kept: with the surname it
lets anyone manage the booking.

The airport's flight information (departures today: flight, destination, time, gate) plugs in through
FlightBoard. FLIGHTS_FILE points at a JSON list, the stand-in until the airport's own feed is connected. Without
one, any flight from this airport today is accepted.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

TITLES = {"MR", "MRS", "MS", "MISS", "MSTR", "DR", "PROF", "MX", "CHD", "INF"}
FLIGHT = re.compile(r"^((?:[A-Z]{2}|[A-Z]\d|\d[A-Z])[A-Z]?)\s*0*(\d{1,4})([A-Z]?)$")  # airline codes have a letter
AIRPORT = re.compile(r"^[A-Z]{3}$")


class NotAllowed(ValueError):
    """A boarding pass or flight that can't be used here (wrong airport, wrong day, unknown flight)."""


@dataclass(frozen=True)
class Leg:
    name: str
    from_airport: str
    to_airport: str
    flight: str
    day_of_year: int
    cabin: str
    seat: str


def _name(raw: str) -> str:
    """'WANJIRU/JANE MS' -> 'Jane Wanjiru'."""
    surname, _, given = raw.strip().partition("/")
    given = " ".join(w for w in given.split() if w not in TITLES)
    return " ".join(w.capitalize() for w in f"{given} {surname}".split())


def _flight(carrier: str, number: str) -> str:
    """'KQ ', '0100 ' -> 'KQ100'."""
    m = FLIGHT.match(f"{carrier.strip()}{number.strip()}".upper())
    if not m:
        raise ValueError("That flight number isn't right")
    return f"{m.group(1)}{int(m.group(2))}{m.group(3)}"


def normalise_flight(text: str) -> str:
    text = re.sub(r"[\s-]", "", (text or "").upper())
    m = FLIGHT.match(text)
    if not m:
        raise ValueError("Enter the flight number as on your boarding pass, e.g. KQ 100")
    return f"{m.group(1)}{int(m.group(2))}{m.group(3)}"


def parse_bcbp(data: str) -> List[Leg]:
    """The legs on a boarding pass barcode (IATA BCBP, format M). Raises ValueError if it isn't one."""
    s = (data or "").rstrip("\r\n")
    if len(s) < 60 or s[0] != "M" or not s[1].isdigit():
        raise ValueError("That isn't a boarding pass barcode")
    legs_total = int(s[1])
    name = _name(s[2:22])
    pos, legs = 23, []  # past the name (20) and the e-ticket indicator (1)
    for _ in range(legs_total):
        f = s[pos:pos + 37]
        if len(f) < 37:
            raise ValueError("That boarding pass barcode is cut short")
        # f[0:7] is the booking reference: skipped on purpose, never stored.
        from_a, to_a, carrier, number, day, cabin, seat = f[7:10], f[10:13], f[13:16], f[16:21], f[21:24], f[24], f[25:29]
        try:
            size = int(f[35:37], 16)
            doy = int(day)
        except ValueError:
            raise ValueError("That boarding pass barcode can't be read")
        if not (AIRPORT.match(from_a) and AIRPORT.match(to_a)) or not 1 <= doy <= 366:
            raise ValueError("That boarding pass barcode can't be read")
        legs.append(Leg(name, from_a, to_a, _flight(carrier, number), doy, cabin.strip(),
                        seat.strip().lstrip("0") or seat.strip()))
        pos += 37 + size
    return legs


def date_from_day(doy: int, today: date) -> date:
    """The date with this day of the year nearest to today (boarding passes leave the year out)."""
    options = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            options.append(date(year, 1, 1) + timedelta(days=doy - 1))
        except ValueError:
            pass
    return min((d for d in options if d.timetuple().tm_yday == doy), key=lambda d: abs((d - today).days))


class FlightBoard:
    """Departures from this airport today. load() gives [{flight, to, departs "HH:MM", gate}]."""

    def __init__(self, path: str = ""):
        self.path = path

    def load(self) -> Optional[List[dict]]:
        if not self.path:
            return None  # no flight information connected: any flight from here today is accepted
        with open(self.path, encoding="utf-8") as f:
            rows = json.load(f)
        return [{**r, "flight": normalise_flight(r["flight"])} for r in rows]


class Airport:
    def __init__(self, code: str, board: FlightBoard, tz: str = "Africa/Nairobi", clock=None):
        if not AIRPORT.match(code or ""):
            raise ValueError("AIRPORT_CODE should be the 3-letter IATA code, e.g. NBO")
        self.code, self.board, self.tz = code, board, ZoneInfo(tz)
        self.clock = clock or (lambda: datetime.now(self.tz))

    def _now(self) -> datetime:
        return self.clock().astimezone(self.tz)

    def _check(self, name: str, flight: str, to_airport: str, day: date, seat: str = "", cabin: str = "", source: str = "") -> dict:
        today = self._now().date()
        if day < today:
            raise NotAllowed(f"This boarding pass is for {day:%d %b}. Use the one for today's flight.")
        if day > today + timedelta(days=1):
            raise NotAllowed(f"This boarding pass is for {day:%d %b}, not today.")
        info = None
        board = self.board.load()
        if board is not None:
            info = next((f for f in board if f["flight"] == flight), None)
            if not info:
                raise NotAllowed(f"We can't find {flight} leaving {self.code} today. Check the flight number, or ask a member of staff.")
            to_airport = to_airport or info.get("to", "")
            if to_airport and info.get("to") and info["to"] != to_airport:
                raise NotAllowed(f"{flight} today goes to {info['to']}, not {to_airport}. Check your boarding pass.")
            if day == today and info.get("departs"):
                h, m = map(int, info["departs"].split(":"))
                if self._now() > self._now().replace(hour=h, minute=m, second=0, microsecond=0):
                    raise NotAllowed(f"{flight} was due to leave at {info['departs']}. Please ask a member of staff.")
        if not name or len(name) > 60:
            raise ValueError("Enter your name as on your boarding pass")
        return {"name": name, "flight": flight, "from": self.code, "to": to_airport or "", "date": day.isoformat(),
                "seat": seat, "cabin": cabin, "departs": (info or {}).get("departs", ""), "gate": (info or {}).get("gate", ""),
                "source": source}

    def from_boarding_pass(self, data: str) -> dict:
        legs = parse_bcbp(data)
        leg = next((l for l in legs if l.from_airport == self.code), None)
        if not leg:
            raise NotAllowed(f"This boarding pass is for a flight from {legs[0].from_airport}, not {self.code}.")
        return self._check(leg.name, leg.flight, leg.to_airport, date_from_day(leg.day_of_year, self._now().date()),
                           leg.seat, leg.cabin, "boarding_pass")

    def from_typed(self, name: str, flight: str, to_airport: str = "", day: Optional[str] = None) -> dict:
        name = " ".join((name or "").split())
        to_airport = (to_airport or "").strip().upper()
        if to_airport and not AIRPORT.match(to_airport):
            raise ValueError("The destination is a 3-letter airport code, e.g. LHR")
        try:
            d = date.fromisoformat(day) if day else self._now().date()
        except ValueError:
            raise ValueError("Enter the flight date")
        return self._check(name, normalise_flight(flight), to_airport, d, source="typed")

    def public(self) -> dict:
        board = self.board.load()
        return {"mode": "airport", "airport": self.code, "flight_board": board is not None}
