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

    def put_image(self, product_id: str, data: bytes, content_type: str, staff: str) -> httpx.Response:
        return self.http.put(f"/products/{product_id}/image", content=data,
                             headers={"Content-Type": content_type or "application/octet-stream", "X-Staff-User": staff},
                             timeout=30.0)

    def get_image(self, product_id: str, if_none_match: str = "") -> httpx.Response:
        return self.http.get(f"/products/{product_id}/image", headers={"If-None-Match": if_none_match} if if_none_match else None)

    def delete_image(self, product_id: str) -> httpx.Response:
        return self.http.delete(f"/products/{product_id}/image")

    def discounts(self) -> List[dict]:
        return self._get("/discounts")

    def add_discount(self, body: dict) -> httpx.Response:
        return self.http.post("/discounts", json=body)

    def end_discount(self, discount_id: int, ended_by: str) -> httpx.Response:
        return self.http.post(f"/discounts/{discount_id}/end", json={"ended_by": ended_by})

    def restock(self, body: dict) -> httpx.Response:
        return self.http.post("/restocks", json=body)

    def find_sale(self, store_id: str, code: str) -> httpx.Response:
        return self.http.get("/sales/lookup", params={"store_id": store_id, "code": code})

    def returnable(self, sale_id: str) -> httpx.Response:
        return self.http.get(f"/sales/{sale_id}/returnable")

    def returns(self, store_id: str, statuses: str) -> List[dict]:
        return self._get("/returns", store_id=store_id, status=statuses)

    def start_return(self, body: dict, staff: str) -> httpx.Response:
        return self.http.post("/returns", json=body, headers={"X-Staff-User": staff} if staff else None)

    def return_action(self, return_id: str, action: str, body: dict) -> httpx.Response:
        """approve, reject or refunded."""
        return self.http.post(f"/returns/{return_id}/{action}", json=body)

    def online_orders(self, store_id: str) -> List[dict]:
        """Paid online orders not collected yet: to pack, or packed and waiting."""
        return self._get("/sales", store_id=store_id, channel="online", status="paid", uncollected="true", limit=200)

    def get_sale(self, sale_id: str) -> dict:
        return self._get(f"/sales/{sale_id}")

    def mark_ready(self, sale_id: str, ready_by: str) -> httpx.Response:
        return self.http.post(f"/sales/{sale_id}/ready", json={"ready_by": ready_by})

    def record_exit(self, sale_id: str, checked_by: str) -> httpx.Response:
        return self.http.post(f"/sales/{sale_id}/exit", json={"checked_by": checked_by})

    def count_stock(self, body: dict) -> httpx.Response:
        return self.http.post("/counts", json=body)

    def counts(self, store_id: str, limit: int = 20) -> List[dict]:
        return self._get(f"/counts/{store_id}", limit=limit)

    def record_count(self, discrepancy_id: int, counted_qty: int, counted_by: str) -> httpx.Response:
        return self.http.post(f"/discrepancies/{discrepancy_id}/count",
                              json={"counted_qty": counted_qty, "counted_by": counted_by})


class DispatchClient:
    """The Dispatch Service: deliveries of online orders and the store's riders."""

    def __init__(self, base_url: str, http: Optional[httpx.Client] = None):
        self.http = http or httpx.Client(base_url=base_url, timeout=5.0)

    def deliveries(self, store_id: str, statuses: List[str]) -> List[dict]:
        r = self.http.get("/deliveries", params={"store_id": store_id, "status": ",".join(statuses)})
        r.raise_for_status()
        return r.json()

    def riders(self) -> List[dict]:
        r = self.http.get("/riders")
        r.raise_for_status()
        return r.json()

    def act(self, sale_id: str, action: str, body: dict) -> httpx.Response:
        """assign, release, hand-over or retry a delivery."""
        return self.http.post(f"/deliveries/{sale_id}/{action}", json=body)


class NotifyClient:
    """The Notification Service: texts sent to online customers."""

    def __init__(self, base_url: str, http: Optional[httpx.Client] = None):
        self.http = http or httpx.Client(base_url=base_url, timeout=5.0)

    def messages(self, limit: int = 50) -> List[dict]:
        r = self.http.get("/messages", params={"limit": limit})
        r.raise_for_status()
        return r.json()

    def resend(self, message_id: int) -> httpx.Response:
        return self.http.post(f"/messages/{message_id}/resend")


class CustomersClient:
    """The Customers Service: loyalty members and the stand-in email outbox."""

    def __init__(self, base_url: str, http: Optional[httpx.Client] = None):
        self.http = http or httpx.Client(base_url=base_url, timeout=5.0)

    def _get(self, path: str, **params):
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def summary(self) -> dict:
        return self._get("/summary")

    def members(self, limit: int = 50) -> List[dict]:
        return self._get("/members", limit=limit)

    def emails(self, limit: int = 30) -> List[dict]:
        return self._get("/emails", limit=limit)

    def settings(self) -> dict:
        return self._get("/settings")


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
