import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    store_id: str
    store_name: str
    inventory_url: str
    payments_url: str
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
        stk_limit_per_phone=int(os.getenv("STK_LIMIT_PER_PHONE", "3")),
    )
