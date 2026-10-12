import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    store_id: str
    store_name: str
    inventory_url: str
    payments_url: str
    customers_url: str = ""  # Customers Service: problem reports; empty turns "Report a problem" off
    store_mode: str = "shop"  # shop, or airport: travellers scan their boarding pass instead of signing in
    airport_code: str = ""  # airport mode: this airport's IATA code, e.g. NBO
    flights_file: str = ""  # airport mode: today's departures (JSON), until the airport's own feed is connected
    store_tz: str = "Africa/Nairobi"
    stk_limit_per_phone: int = 3
    stk_limit_window_s: int = 600
    max_qty_per_item: int = 20
    max_lines: int = 50


def load_settings() -> Settings:
    return Settings(
        store_id=os.getenv("STORE_ID", "001"),
        store_name=os.getenv("STORE_NAME", "Smart Shopping"),
        inventory_url=os.getenv("INVENTORY_URL", "http://localhost:8010"),
        payments_url=os.getenv("PAYMENTS_URL", "http://localhost:8000"),
        customers_url=os.getenv("CUSTOMERS_URL", ""),
        store_mode=(os.getenv("STORE_MODE") or "shop").strip().lower(),
        airport_code=(os.getenv("AIRPORT_CODE") or "").strip().upper(),
        flights_file=os.getenv("FLIGHTS_FILE", ""),
        store_tz=os.getenv("STORE_TZ") or "Africa/Nairobi",
        stk_limit_per_phone=int(os.getenv("STK_LIMIT_PER_PHONE", "3")),
    )
