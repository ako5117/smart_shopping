import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    store_id: str
    store_tz: str
    inventory_url: str
    shelf_events_path: str
    shelf_config_path: str
    low_stock_threshold: int = 5
    event_limit: int = 200
    dispatch_url: str = ""  # Dispatch Service; empty: no deliveries


def load_settings() -> Settings:
    return Settings(
        store_id=os.getenv("STORE_ID", "001"),
        store_tz=os.getenv("STORE_TZ", "Africa/Nairobi"),
        inventory_url=os.getenv("INVENTORY_URL", "http://localhost:8010"),
        shelf_events_path=os.getenv("SHELF_EVENTS_PATH", "shelf_events.jsonl"),
        shelf_config_path=os.getenv("SHELF_CONFIG_PATH", "config.example.json"),
        low_stock_threshold=int(os.getenv("LOW_STOCK_THRESHOLD", "5")),
        dispatch_url=os.getenv("DISPATCH_URL", ""),
    )
