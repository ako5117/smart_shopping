import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    store_id: str
    store_name: str
    inventory_url: str
    payments_url: str
    dispatch_url: str = ""  # Dispatch Service; empty: collection only, no delivery
    shelf_events_path: str = ""  # the Shelf Service's event log; empty: no live shelf signal
    picked_window_min: int = 5  # items picked up this recently count as in a shopper's basket
    in_store_buffer: int = 1  # kept back from online sale per product, in case the shelf count is off
    few_left: int = 5  # at or below this, the shop says "Only N left"
    order_hold_min: int = 15  # how long an unpaid order holds its items (Inventory Service HOLD_MINUTES)
    stk_limit_per_phone: int = 3
    stk_limit_window_s: int = 600
    max_qty_per_item: int = 20
    max_lines: int = 30
    products_cache_s: float = 30  # prices and names
    stock_cache_s: float = 5  # stock, holds and shelf activity


def load_settings() -> Settings:
    return Settings(
        store_id=os.getenv("STORE_ID", "001"),
        store_name=os.getenv("STORE_NAME", "Smart Shopping"),
        inventory_url=os.getenv("INVENTORY_URL", "http://localhost:8010"),
        payments_url=os.getenv("PAYMENTS_URL", "http://localhost:8000"),
        dispatch_url=os.getenv("DISPATCH_URL", ""),
        # Standard tier (no shelf sensors): no live shelf signal, even if an old event log is lying around.
        shelf_events_path=os.getenv("SHELF_EVENTS_PATH", "") if os.getenv("STORE_TIER", "smart").strip().lower() != "standard" else "",
        picked_window_min=int(os.getenv("PICKED_WINDOW_MIN", "5")),
        in_store_buffer=int(os.getenv("IN_STORE_BUFFER", "1")),
        few_left=int(os.getenv("FEW_LEFT", "5")),
        stk_limit_per_phone=int(os.getenv("STK_LIMIT_PER_PHONE", "3")),
    )
