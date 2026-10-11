"""Delivery fees by distance from the store, express or scheduled, and how far the store delivers.

    scheduled fee = base + per_km x distance, rounded up to the next KES 10
    express fee   = scheduled fee x express multiplier, rounded up to the next KES 10

Distance is the straight line between the store and the customer's location, times a road factor (roads
wind; 1.3 is a common rule of thumb for a city). Beyond max_km the store doesn't deliver, and the online
shop says so before the customer starts shopping.

Express goes out as soon as it's packed (about express_minutes), during delivery hours. Scheduled is a
slot of slot_hours, today or the next days_ahead days, starting at least lead_minutes from now.

Every number is a setting (environment variables, see from_env), so the store can change them.
"""

import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

EARTH_KM = 6371.0
TIERS = ("express", "scheduled")


@dataclass(frozen=True)
class Pricing:
    store_lat: float = -1.2864  # demo: central Nairobi. Set STORE_LAT / STORE_LNG to the store's own location.
    store_lng: float = 36.8172
    base_kes: int = 100
    per_km_kes: int = 20
    express_multiplier: float = 1.5
    max_km: float = 20.0
    road_factor: float = 1.3
    open_hour: int = 8
    close_hour: int = 20
    slot_hours: int = 2
    days_ahead: int = 2
    lead_minutes: int = 60
    express_minutes: int = 60
    tz: str = "Africa/Nairobi"

    def __post_init__(self):
        if not (-90 <= self.store_lat <= 90 and -180 <= self.store_lng <= 180):
            raise ValueError("STORE_LAT / STORE_LNG aren't a valid location")
        if not (0 <= self.open_hour < self.close_hour <= 24) or self.slot_hours < 1:
            raise ValueError("Delivery hours should look like 08-20, with slots of at least 1 hour")
        if self.max_km <= 0 or self.base_kes < 0 or self.per_km_kes < 0 or self.express_multiplier < 1:
            raise ValueError("Delivery prices and the distance limit must be positive (express at least 1x)")

    # ------------------------------------------------------------------ fees

    def distance_km(self, lat: float, lng: float) -> float:
        """Approximate road distance from the store, in km."""
        p1, p2 = math.radians(self.store_lat), math.radians(lat)
        dp, dl = p2 - p1, math.radians(lng - self.store_lng)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * EARTH_KM * math.asin(math.sqrt(a)) * self.road_factor

    def fee(self, km: float, tier: str) -> int:
        scheduled = _up_to_10(self.base_kes + self.per_km_kes * km)
        return scheduled if tier == "scheduled" else _up_to_10(scheduled * self.express_multiplier)

    # ----------------------------------------------------------------- times

    def _local(self, now: datetime) -> datetime:
        return now.astimezone(ZoneInfo(self.tz))

    def express_open(self, now: datetime) -> bool:
        """Express is taken while there's time to deliver before closing."""
        t = self._local(now)
        last = t.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=self.close_hour) \
            - timedelta(minutes=self.express_minutes)
        return t.hour >= self.open_hour and t < last

    def slots(self, now: datetime) -> List[dict]:
        """Scheduled slots still open: {start, end (ISO, UTC), label}."""
        local_now = self._local(now)
        earliest = local_now + timedelta(minutes=self.lead_minutes)
        out = []
        for day in range(self.days_ahead + 1):
            d = (local_now + timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
            for h in range(self.open_hour, self.close_hour, self.slot_hours):
                start = d + timedelta(hours=h)
                end = min(start + timedelta(hours=self.slot_hours), d + timedelta(hours=self.close_hour))
                if start < earliest:
                    continue
                day_word = "Today" if day == 0 else "Tomorrow" if day == 1 else start.strftime("%A")
                out.append({"start": start.astimezone(timezone.utc).isoformat(), "end": end.astimezone(timezone.utc).isoformat(),
                            "label": f"{day_word} {start:%H:%M}–{end:%H:%M}"})
        return out

    def slot(self, start_iso: Optional[str], now: datetime) -> dict:
        """The open slot starting at start_iso, or ValueError."""
        for s in self.slots(now):
            if start_iso and _same_time(s["start"], start_iso):
                return s
        raise ValueError("That delivery slot isn't available any more. Choose another.")

    # ----------------------------------------------------------------- quote

    def quote(self, lat: float, lng: float, now: datetime) -> dict:
        """Can the store deliver here, and for how much? What the online shop shows before and at checkout."""
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("That isn't a valid location")
        km = round(self.distance_km(lat, lng), 1)
        if km > self.max_km:
            return {"deliverable": False, "distance_km": km, "max_km": self.max_km,
                    "reason": f"You're about {km:g} km from the store. We deliver within {self.max_km:g} km, "
                              f"so this order would need to be collected from the store."}
        slots = self.slots(now)
        express = self.express_open(now)
        return {"deliverable": bool(slots or express), "distance_km": km, "max_km": self.max_km,
                "reason": "" if (slots or express) else "No delivery times are left. Please try again later.",
                "express": {"available": express, "fee_kes": self.fee(km, "express"), "eta_minutes": self.express_minutes},
                "scheduled": {"available": bool(slots), "fee_kes": self.fee(km, "scheduled"), "slots": slots}}

    def public(self) -> dict:
        """The settings the online shop shows (no secrets here)."""
        return {"store": {"lat": self.store_lat, "lng": self.store_lng}, "max_km": self.max_km, "base_kes": self.base_kes,
                "per_km_kes": self.per_km_kes, "express_multiplier": self.express_multiplier, "road_factor": self.road_factor,
                "hours": f"{self.open_hour:02d}:00–{self.close_hour:02d}:00", "express_minutes": self.express_minutes}


def from_env() -> Pricing:
    env = os.getenv
    hours = (env("DELIVERY_HOURS") or "08-20").split("-")
    try:
        return Pricing(
            store_lat=float(env("STORE_LAT") or Pricing.store_lat), store_lng=float(env("STORE_LNG") or Pricing.store_lng),
            base_kes=int(env("DELIVERY_BASE_KES") or Pricing.base_kes), per_km_kes=int(env("DELIVERY_PER_KM_KES") or Pricing.per_km_kes),
            express_multiplier=float(env("DELIVERY_EXPRESS_MULTIPLIER") or Pricing.express_multiplier),
            max_km=float(env("DELIVERY_MAX_KM") or Pricing.max_km), road_factor=float(env("DELIVERY_ROAD_FACTOR") or Pricing.road_factor),
            open_hour=int(hours[0]), close_hour=int(hours[1]), slot_hours=int(env("DELIVERY_SLOT_HOURS") or Pricing.slot_hours),
            express_minutes=int(env("DELIVERY_EXPRESS_MINUTES") or Pricing.express_minutes),
            tz=env("STORE_TZ") or Pricing.tz)
    except (IndexError, ValueError) as e:
        raise ValueError(f"Delivery settings: {e}") from e


def _up_to_10(x: float) -> int:
    return int(math.ceil(x / 10.0 - 1e-9) * 10)


def _same_time(a: str, b: str) -> bool:
    try:
        return datetime.fromisoformat(a) == datetime.fromisoformat(b)
    except ValueError:
        return False
