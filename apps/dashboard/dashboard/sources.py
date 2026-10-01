"""Where the dashboard's data comes from: the Inventory Service over HTTP, and the Shelf Service's
event log and catalogue on local disk (both run at the store)."""

import json
import os
from typing import Dict, List, Optional

import httpx


class InventoryClient:
    def __init__(self, base_url: str, http: Optional[httpx.Client] = None):
        self.http = http or httpx.Client(base_url=base_url, timeout=5.0)

    def _get(self, path: str, **params):
        r = self.http.get(path, params=params or None)
        r.raise_for_status()
        return r.json()

    def products(self) -> List[dict]:
        return self._get("/products")

    def stock(self, store_id: str) -> Dict[str, int]:
        return self._get(f"/stock/{store_id}")

    def sales(self, store_id: str, limit: int = 100) -> List[dict]:
        return self._get("/sales", store_id=store_id, limit=limit)

    def sync_status(self, store_id: str) -> dict:
        return self._get(f"/sync/{store_id}")

    def discrepancies(self, store_id: str) -> List[dict]:
        return self._get(f"/discrepancies/{store_id}")

    def save_product(self, product_id: str, body: dict) -> httpx.Response:
        return self.http.put(f"/products/{product_id}", json={"product_id": product_id, **body})

    def restock(self, body: dict) -> httpx.Response:
        return self.http.post("/restocks", json=body)

    def find_sale(self, store_id: str, code: str) -> httpx.Response:
        return self.http.get("/sales/lookup", params={"store_id": store_id, "code": code})

    def record_exit(self, sale_id: str, checked_by: str) -> httpx.Response:
        return self.http.post(f"/sales/{sale_id}/exit", json={"checked_by": checked_by})

    def record_count(self, discrepancy_id: int, counted_qty: int, counted_by: str) -> httpx.Response:
        return self.http.post(f"/discrepancies/{discrepancy_id}/count",
                              json={"counted_qty": counted_qty, "counted_by": counted_by})


def load_catalogue(path: str) -> dict:
    """Products and zones from the Shelf Service catalogue (services/shelf/config.example.json format)."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {"products": {p["product_id"]: p for p in raw.get("products", [])},
            "zones": raw.get("zones", [])}


def read_shelf_events(path: str, limit: int) -> List[dict]:
    """The last `limit` events from the Shelf Service's JSON-lines log, oldest first.

    Reads from the end of the file so a long-running store's log stays cheap to poll.
    A partly written last line or a corrupt line is skipped.
    """
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        block, data = 64 * 1024, b""
        pos = end
        while pos > 0 and data.count(b"\n") <= limit:
            pos = max(0, pos - block)
            f.seek(pos)
            data = f.read(end - pos)
    lines = data.splitlines()
    if pos > 0:
        lines = lines[1:]  # first line may be cut in half
    events = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events
