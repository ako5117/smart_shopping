"""Store dashboard: one page showing the shelves, committed stock, sales, retailer sync and
discrepancies for a single store.

    uvicorn dashboard.app:create_app --factory --port 8020

Each data source is read independently, so if the Inventory Service is down the shelf view
still works (and the other way round). The page shows which source failed.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import Settings, load_settings
from .sources import InventoryClient, load_catalogue, read_shelf_events

log = logging.getLogger("dashboard")
STATIC = Path(__file__).parent / "static"


class CountIn(BaseModel):
    counted_qty: int = Field(..., ge=0)
    counted_by: str = Field(..., min_length=1)


def _attempt(errors: dict, source: str, fn: Callable, default):
    try:
        return fn()
    except (httpx.HTTPError, OSError, ValueError) as e:
        log.warning("%s unavailable: %s", source, e)
        errors[source] = str(e) or e.__class__.__name__
        return default


def build_overview(settings: Settings, inventory: InventoryClient, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(settings.store_tz)
    store = settings.store_id
    errors: dict = {}

    # ---------------------------------------------------------------- shelf (local)
    catalogue = _attempt(errors, "shelf_catalogue", lambda: load_catalogue(settings.shelf_config_path),
                         {"products": {}, "zones": []})
    events = _attempt(errors, "shelf_events",
                      lambda: read_shelf_events(settings.shelf_events_path, settings.event_limit), [])

    # ---------------------------------------------------------------- inventory (HTTP)
    def inventory_data():
        return {"products": inventory.products(), "stock": inventory.stock(store),
                "sales": inventory.sales(store, 100), "sync": inventory.sync_status(store),
                "discrepancies": inventory.discrepancies(store)}

    inv = _attempt(errors, "inventory", inventory_data, None)

    names = {pid: p.get("name", pid) for pid, p in catalogue["products"].items()}
    if inv:
        names.update({p["product_id"]: p["name"] for p in inv["products"]})
    stock = inv["stock"] if inv else {}

    # ---------------------------------------------------------------- stock table
    product_ids = sorted(set(stock) | set(names))
    stock_rows = [{"product_id": pid, "name": names.get(pid, pid), "qty": stock.get(pid, 0),
                   "low": stock.get(pid, 0) <= settings.low_stock_threshold}
                  for pid in product_ids] if inv else []

    # ---------------------------------------------------------------- shelf zones
    last_by_zone: dict = {}
    alerts_by_zone: dict = {}
    for e in events:
        last_by_zone[e.get("zone_id")] = e
        if e.get("alert") or e.get("needs_review"):
            alerts_by_zone[e.get("zone_id")] = alerts_by_zone.get(e.get("zone_id"), 0) + 1
    zones = [{
        "zone_id": z["zone_id"],
        "shelf_id": z.get("shelf_id", ""),
        "products": [{"product_id": pid, "name": names.get(pid, pid),
                      "committed_qty": stock.get(pid) if inv else None,
                      "low": inv is not None and stock.get(pid, 0) <= settings.low_stock_threshold}
                     for pid in z.get("product_ids", [])],
        "last_event": last_by_zone.get(z["zone_id"]),
        "attention": alerts_by_zone.get(z["zone_id"], 0),
    } for z in catalogue["zones"]]

    review = [e for e in reversed(events) if e.get("alert") or e.get("needs_review")][:50]

    # ---------------------------------------------------------------- sales today
    sales = inv["sales"] if inv else []
    today = now.astimezone(tz).date()

    def is_today(s):
        try:
            return datetime.fromisoformat(s["created_at"]).astimezone(tz).date() == today
        except (KeyError, ValueError):
            return False

    todays = [s for s in sales if is_today(s)]
    paid = [s for s in todays if s["status"] == "paid"]
    for s in sales:
        for it in s.get("items", []):
            it["name"] = names.get(it["product_id"], it["product_id"])

    discrepancies = inv["discrepancies"] if inv else []
    for d in discrepancies:
        d["name"] = names.get(d["product_id"], d["product_id"])

    return {
        "store_id": store,
        "generated_at": now.isoformat(),
        "errors": errors,
        "summary": {
            "paid_sales_today": len(paid),
            "items_sold_today": sum(it["qty"] for s in paid for it in s.get("items", [])),
            "awaiting_payment": sum(1 for s in todays if s["status"] == "pending_payment"),
            "low_stock": sum(1 for r in stock_rows if r["low"]),
            "open_discrepancies": len(discrepancies),
            "needs_attention": len(review),
            "sync_pending": inv["sync"]["pending"] if inv else None,
        },
        "zones": zones,
        "review": review,
        "events": list(reversed(events))[:50],
        "stock": stock_rows,
        "sales": sales[:20],
        "sync": inv["sync"] if inv else None,
        "discrepancies": discrepancies,
        "low_stock_threshold": settings.low_stock_threshold,
    }


def create_app(settings: Optional[Settings] = None, inventory: Optional[InventoryClient] = None) -> FastAPI:
    settings = settings or load_settings()
    inventory = inventory or InventoryClient(settings.inventory_url)
    app = FastAPI(title="Smart Shopping - Store Dashboard")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/overview")
    def overview():
        return build_overview(settings, inventory)

    @app.post("/api/discrepancies/{discrepancy_id}/count")
    def record_count(discrepancy_id: int, body: CountIn):
        try:
            r = inventory.record_count(discrepancy_id, body.counted_qty, body.counted_by.strip())
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Inventory Service unavailable: {e}")
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
            raise HTTPException(r.status_code, detail)
        return r.json()

    return app
