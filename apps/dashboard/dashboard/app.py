"""Store dashboard: one page showing the shelves, committed stock, sales, retailer sync and
discrepancies for a single store.

    uvicorn dashboard.app:create_app --factory --port 8020

Each data source is read independently, so if the Inventory Service is down the shelf view
still works (and the other way round). The page shows which source failed.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, load_settings
from .sources import DispatchClient, InventoryClient, load_catalogue, read_shelf_events

log = logging.getLogger("dashboard")
STATIC = Path(__file__).parent / "static"


class CountIn(BaseModel):
    counted_qty: int = Field(..., ge=0)
    counted_by: str = Field("", max_length=60, description="Only used when not signed in through the proxy")


class ProductIn(BaseModel):
    ean13: str = Field(..., pattern=r"^\d{13}$")
    name: str = Field(..., min_length=1, max_length=120)
    price_kes: Optional[int] = Field(None, gt=0)
    category: Optional[str] = Field(None, max_length=40)


class RestockIn(BaseModel):
    ean13: str = Field(..., pattern=r"^\d{13}$")
    qty: int = Field(..., gt=0, le=10000)
    scan_id: str = Field(..., min_length=8, max_length=64, description="Made by the page once per restock, so a retry isn't counted twice")


class ExitIn(BaseModel):
    checked_by: str = Field("", max_length=60, description="Only used when not signed in through the proxy")


class ReadyIn(BaseModel):
    packed_by: str = Field("", max_length=60, description="Only used when not signed in through the proxy")


class AssignIn(ReadyIn):
    rider_id: str = Field(..., min_length=1, max_length=60)


@dataclass(frozen=True)
class Staff:
    """Who is using the dashboard. The proxy (deploy/Caddyfile) sets the headers from the staff login and
    overwrites anything a browser sends. Without them the dashboard is being run directly, e.g. in
    development, and acts as a manager with the name typed into each form."""
    name: Optional[str]
    role: str

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"


def current_staff(user: Optional[str] = Header(None, alias="X-Staff-User", include_in_schema=False),
                  role: Optional[str] = Header(None, alias="X-Staff-Role", include_in_schema=False)) -> Staff:
    if not user:
        return Staff(None, "manager")
    return Staff(user, "manager" if role == "manager" else "staff")


def require_manager(staff: Staff, what: str) -> None:
    if not staff.is_manager:
        raise HTTPException(403, f"Only managers can {what}. Ask a manager, or have them make you one.")


def acting_name(staff: Staff, typed: str) -> str:
    name = staff.name or typed.strip()
    if not name:
        raise HTTPException(422, "Enter your name.")
    return name


def _passthrough(r: httpx.Response):
    """Return the Inventory Service's answer, or raise its error with the same status and message."""
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        if isinstance(detail, list):  # validation errors
            detail = "Please check what you entered."
        raise HTTPException(r.status_code, detail)
    return r.json()


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


def create_app(settings: Optional[Settings] = None, inventory: Optional[InventoryClient] = None,
               dispatch: Optional[DispatchClient] = None) -> FastAPI:
    settings = settings or load_settings()
    inventory = inventory or InventoryClient(settings.inventory_url)
    if dispatch is None and settings.dispatch_url:
        dispatch = DispatchClient(settings.dispatch_url)
    app = FastAPI(title="Smart Shopping - Store Dashboard")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/products")
    def products_page():
        return FileResponse(STATIC / "products.html")

    @app.get("/exit")
    def exit_page():
        return FileResponse(STATIC / "exit.html")

    @app.get("/orders")
    def orders_page():
        return FileResponse(STATIC / "orders.html")

    def call(fn, *args):
        try:
            return _passthrough(fn(*args))
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Inventory Service unavailable: {e}")

    def catalogue() -> dict:
        try:
            return {p["product_id"]: p for p in inventory.products()}
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Inventory Service unavailable: {e}")

    @app.get("/api/products")
    def list_products():
        products = catalogue()
        try:
            stock = inventory.stock(settings.store_id)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Inventory Service unavailable: {e}")
        return [{**p, "stock": stock.get(pid, 0)} for pid, p in sorted(products.items(), key=lambda kv: kv[1]["name"].lower())]

    @app.get("/api/shelf-products")
    def shelf_products():
        """Products the shelf sensors already know, so new products reuse the same product code."""
        try:
            products = load_catalogue(settings.shelf_config_path)["products"].values()
        except (OSError, ValueError):
            return []
        return [{"product_id": p["product_id"], "ean13": p["ean13"], "name": p.get("name", p["product_id"])} for p in products]

    @app.get("/api/me")
    def me(staff: Staff = Depends(current_staff)):
        return {"name": staff.name, "role": staff.role, "is_manager": staff.is_manager}

    @app.put("/api/products/{product_id}")
    def save_product(product_id: str, body: ProductIn, staff: Staff = Depends(current_staff)):
        require_manager(staff, "add products or change prices")
        # A missing price or category keeps the current one.
        return call(inventory.save_product, product_id, {**body.model_dump(exclude_none=True), "changed_by": staff.name or ""})

    @app.post("/api/restocks")
    def restock(body: RestockIn, staff: Staff = Depends(current_staff)):
        return call(inventory.restock, {**body.model_dump(), "store_id": settings.store_id,
                                        "recorded_by": staff.name or ""})

    def priced(sale: dict, products: Optional[dict] = None) -> dict:
        products = products or catalogue()
        lines = []
        for it in sale.get("items", []):
            p = products.get(it["product_id"], {})
            price = it.get("unit_price_kes") or p.get("price_kes")  # what the customer paid
            lines.append({**it, "name": p.get("name", it["product_id"]), "price_kes": price,
                          "line_total": price * it["qty"] if price else None})
        return {**sale, "items": lines, "item_count": sum(l["qty"] for l in lines)}

    @app.get("/api/exit/lookup")
    def exit_lookup(code: str):
        return priced(call(inventory.find_sale, settings.store_id, code))

    @app.post("/api/exit/{sale_id}")
    def exit_confirm(sale_id: str, body: ExitIn, staff: Staff = Depends(current_staff)):
        return priced(call(inventory.record_exit, sale_id, acting_name(staff, body.checked_by)))

    @app.get("/api/orders")
    def online_orders():
        """Paid online orders, oldest first: still in the store (to pack, or ready for the customer or a rider),
        and out with a rider. Each item says where it sits on the shelves."""
        try:
            zones = load_catalogue(settings.shelf_config_path)["zones"]
        except (OSError, ValueError):
            zones = []
        where = {}
        for z in zones:
            for pid in z.get("product_ids", []):
                where.setdefault(pid, []).append(z["zone_id"])
        try:
            orders = inventory.online_orders(settings.store_id)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Inventory Service unavailable: {e}")

        deliveries, riders, dispatch_error = {}, [], None
        if dispatch is not None:
            try:
                deliveries = {d["sale_id"]: d for d in dispatch.deliveries(
                    settings.store_id, ["unassigned", "assigned", "picked_up", "failed"])}
                riders = dispatch.riders()
            except httpx.HTTPError as e:
                log.warning("Dispatch unavailable: %s", e)
                dispatch_error = "Deliveries can't be shown right now: the Dispatch Service is unavailable."

        products = catalogue()

        def view(sale: dict) -> dict:
            sale = priced(sale, products)
            for it in sale["items"]:
                it["zones"] = where.get(it["product_id"], [])
            sale["total"] = sum(it["line_total"] or 0 for it in sale["items"]) + (sale.get("delivery_fee_kes") or 0)
            sale["stage"] = "ready" if sale.get("ready_at") else "packing"
            sale["delivery"] = deliveries.get(sale["sale_id"])
            return sale

        in_store = [view(s) for s in sorted(orders, key=lambda s: s["created_at"])]
        out = []
        for d in deliveries.values():
            if d["status"] in ("picked_up", "failed"):
                try:
                    out.append(view(inventory.get_sale(d["sale_id"])))
                except httpx.HTTPError as e:
                    log.warning("Inventory unavailable for %s: %s", d["sale_id"], e)
        return {"orders": in_store, "out": sorted(out, key=lambda s: s["created_at"]), "riders": riders,
                "deliveries_on": dispatch is not None, "dispatch_error": dispatch_error}

    @app.post("/api/orders/{sale_id}/ready")
    def order_ready(sale_id: str, body: ReadyIn, staff: Staff = Depends(current_staff)):
        return priced(call(inventory.mark_ready, sale_id, acting_name(staff, body.packed_by)))

    def deliver_action(sale_id: str, action: str, body: dict):
        if dispatch is None:
            raise HTTPException(404, "Deliveries aren't set up for this store.")
        try:
            return _passthrough(dispatch.act(sale_id, action, body))
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Dispatch Service unavailable: {e}")

    @app.post("/api/orders/{sale_id}/assign")
    def assign_rider(sale_id: str, body: AssignIn, staff: Staff = Depends(current_staff)):
        return deliver_action(sale_id, "assign", {"rider_id": body.rider_id, "by": acting_name(staff, body.packed_by)})

    @app.post("/api/orders/{sale_id}/unassign")
    def unassign_rider(sale_id: str, body: ReadyIn, staff: Staff = Depends(current_staff)):
        return deliver_action(sale_id, "release", {"by": acting_name(staff, body.packed_by)})

    @app.post("/api/orders/{sale_id}/hand-over")
    def hand_over(sale_id: str, body: ReadyIn, staff: Staff = Depends(current_staff)):
        """The packed order goes out with its rider. Recorded as leaving the store, like an exit check."""
        return deliver_action(sale_id, "hand-over", {"by": acting_name(staff, body.packed_by)})

    @app.post("/api/orders/{sale_id}/retry")
    def retry_delivery(sale_id: str, body: ReadyIn, staff: Staff = Depends(current_staff)):
        """After a failed delivery or locked code: staff spoke to the customer, the rider tries again."""
        return deliver_action(sale_id, "retry", {"by": acting_name(staff, body.packed_by)})

    @app.get("/api/overview")
    def overview():
        return build_overview(settings, inventory)

    @app.post("/api/discrepancies/{discrepancy_id}/count")
    def record_count(discrepancy_id: int, body: CountIn, staff: Staff = Depends(current_staff)):
        require_manager(staff, "correct stock counts")
        name = acting_name(staff, body.counted_by)
        try:
            r = inventory.record_count(discrepancy_id, body.counted_qty, name)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Inventory Service unavailable: {e}")
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
            raise HTTPException(r.status_code, detail)
        return r.json()

    return app
