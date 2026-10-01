"""HTTP API for the Inventory Service.

    uvicorn inventory.api:create_app --factory --port 8010
"""

import os
from typing import List, Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .db import Database, database_from_env
from .service import Conflict, Inventory, Item, NotEnoughStock, NotFound
from . import sync


class ProductIn(BaseModel):
    product_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    ean13: str = Field(..., pattern=r"^\d{13}$")
    name: str = Field(..., min_length=1, max_length=120)
    price_kes: Optional[int] = Field(None, gt=0, description="Shelf price in whole KES; omit to keep the current price")
    changed_by: str = Field("", max_length=60, description="Who made the change (defaults to the signed-in staff member)")
    category: Optional[str] = Field(None, max_length=40, description="Shelf category, e.g. Dairy; used to suggest substitutes. Omit to keep the current one")


class RestockIn(BaseModel):
    store_id: str
    ean13: str = Field(..., pattern=r"^\d{13}$")
    qty: int = Field(..., gt=0)
    scan_id: str = Field(..., min_length=1, description="Unique per scan, so a re-sent scan is not counted twice")
    recorded_by: str = Field("", max_length=60, description="Who restocked (defaults to the signed-in staff member)")


class ItemIn(BaseModel):
    product_id: str
    qty: int = Field(..., gt=0)


class SaleIn(BaseModel):
    sale_id: str = Field(..., min_length=1, max_length=64)
    store_id: str
    items: List[ItemIn] = Field(..., min_length=1)
    channel: Literal["in_store", "online"] = "in_store"
    customer_name: str = Field("", max_length=60, description="Online orders: who collects")
    check_stock: bool = Field(False, description="Refuse the sale (409) unless every item is available")


class ReadyIn(BaseModel):
    ready_by: str = Field("", max_length=60, description="Who packed it (defaults to the signed-in staff member)")


class CommitIn(BaseModel):
    payment_ref: str = Field(..., min_length=1)


class ReturnIn(BaseModel):
    product_id: str
    qty: int = Field(..., gt=0)
    return_id: str


class AdjustIn(BaseModel):
    store_id: str
    product_id: str
    qty_change: int
    reason: str = Field(..., min_length=3)
    ref: str
    recorded_by: str = Field("", max_length=60, description="Who made the correction (defaults to the signed-in staff member)")


class SnapshotIn(BaseModel):
    store_id: str
    stock: dict
    tolerance: int = Field(0, ge=0)


class ExitIn(BaseModel):
    checked_by: str = Field(..., min_length=1, max_length=60)


class CountIn(BaseModel):
    counted_qty: int = Field(..., ge=0)
    counted_by: str


# Set by the proxy from the signed-in staff login (deploy/Caddyfile); used when a request doesn't name anyone.
StaffUser = Header(None, alias="X-Staff-User", include_in_schema=False)


def who(named: str, header: Optional[str]) -> str:
    return (named or header or "").strip()


def create_app(db: Optional[Database] = None) -> FastAPI:
    db = db or database_from_env()
    inv = Inventory(db)
    app = FastAPI(title="Smart Shopping - Inventory Service")

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except NotEnoughStock as e:
            raise HTTPException(409, {"message": str(e), "short": e.short})
        except Conflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/products")
    def list_products():
        return inv.products()

    @app.put("/products/{product_id}")
    def put_product(product_id: str, p: ProductIn, staff: Optional[str] = StaffUser):
        if p.product_id != product_id:
            raise HTTPException(422, "product_id in path and body differ")
        guard(inv.upsert_product, p.product_id, p.ean13, p.name, p.price_kes, who(p.changed_by, staff), p.category)
        return {"product_id": product_id}

    @app.get("/products/{product_id}/price-history")
    def price_history(product_id: str):
        return inv.price_history(product_id)

    @app.post("/restocks")
    def restock(r: RestockIn, staff: Optional[str] = StaffUser):
        return guard(inv.restock, r.store_id, r.ean13, r.qty, r.scan_id, who(r.recorded_by, staff))

    @app.get("/stock/{store_id}")
    def stock_levels(store_id: str):
        return inv.stock_levels(store_id)

    @app.get("/availability/{store_id}")
    def availability(store_id: str):
        """Committed stock per product, and how much unpaid orders are holding."""
        return inv.availability(store_id)

    @app.get("/stock/{store_id}/{product_id}")
    def stock(store_id: str, product_id: str):
        return {"product_id": product_id, "qty": inv.stock(store_id, product_id),
                "history": inv.history(store_id, product_id, 20)}

    @app.post("/sales", status_code=201)
    def create_sale(s: SaleIn):
        return guard(inv.create_sale, s.sale_id, s.store_id, [Item(i.product_id, i.qty) for i in s.items],
                     s.channel, s.customer_name, s.check_stock)

    @app.get("/sales")
    def list_sales(store_id: str, limit: int = Query(50, ge=1, le=500),
                   channel: Optional[Literal["in_store", "online"]] = None,
                   status: Optional[Literal["pending_payment", "paid", "cancelled"]] = None,
                   uncollected: bool = False):
        return inv.recent_sales(store_id, limit, channel, status, uncollected)

    @app.get("/sales/lookup")
    def lookup_sale(store_id: str, code: str):
        return guard(inv.find_sale_by_code, store_id, code)

    @app.get("/sales/{sale_id}")
    def get_sale(sale_id: str):
        return guard(inv.sale, sale_id)

    @app.post("/sales/{sale_id}/commit")
    def commit_sale(sale_id: str, body: CommitIn):
        return guard(inv.commit_sale, sale_id, body.payment_ref)

    @app.post("/sales/{sale_id}/exit")
    def exit_sale(sale_id: str, body: ExitIn):
        return guard(inv.record_exit, sale_id, body.checked_by.strip())

    @app.post("/sales/{sale_id}/ready")
    def ready_sale(sale_id: str, body: ReadyIn, staff: Optional[str] = StaffUser):
        return guard(inv.mark_ready, sale_id, who(body.ready_by, staff))

    @app.post("/sales/{sale_id}/cancel")
    def cancel_sale(sale_id: str):
        return guard(inv.cancel_sale, sale_id)

    @app.post("/sales/{sale_id}/returns")
    def return_items(sale_id: str, r: ReturnIn):
        return guard(inv.return_items, sale_id, r.product_id, r.qty, r.return_id)

    @app.post("/adjustments")
    def adjust(a: AdjustIn, staff: Optional[str] = StaffUser):
        return guard(inv.adjust, a.store_id, a.product_id, a.qty_change, a.reason, a.ref, who(a.recorded_by, staff))

    @app.post("/reconcile")
    def reconcile(s: SnapshotIn):
        return {"differences": guard(sync.reconcile, db, s.store_id, {k: int(v) for k, v in s.stock.items()}, s.tolerance)}

    @app.get("/sync/{store_id}")
    def sync_status(store_id: str):
        return sync.sync_status(db, store_id)

    @app.get("/discrepancies/{store_id}")
    def discrepancies(store_id: str):
        return sync.open_discrepancies(db, store_id)

    @app.post("/discrepancies/{discrepancy_id}/count")
    def resolve(discrepancy_id: int, c: CountIn):
        return guard(sync.resolve_with_count, db, inv, discrepancy_id, c.counted_qty, c.counted_by)

    return app
