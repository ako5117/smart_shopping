"""HTTP API for the Inventory Service.

    uvicorn inventory.api:create_app --factory --port 8010
"""

import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .db import Database
from .service import Conflict, Inventory, Item, NotFound
from . import sync


class ProductIn(BaseModel):
    product_id: str
    ean13: str = Field(..., pattern=r"^\d{13}$")
    name: str


class RestockIn(BaseModel):
    store_id: str
    ean13: str = Field(..., pattern=r"^\d{13}$")
    qty: int = Field(..., gt=0)
    scan_id: str = Field(..., min_length=1, description="Unique per scan, so a re-sent scan is not counted twice")


class ItemIn(BaseModel):
    product_id: str
    qty: int = Field(..., gt=0)


class SaleIn(BaseModel):
    sale_id: str = Field(..., min_length=1, max_length=64)
    store_id: str
    items: List[ItemIn] = Field(..., min_length=1)


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


class SnapshotIn(BaseModel):
    store_id: str
    stock: dict
    tolerance: int = Field(0, ge=0)


class CountIn(BaseModel):
    counted_qty: int = Field(..., ge=0)
    counted_by: str


def create_app(db: Optional[Database] = None) -> FastAPI:
    db = db or Database(os.getenv("INVENTORY_DB_PATH", "inventory.db"))
    inv = Inventory(db)
    app = FastAPI(title="Smart Shopping - Inventory Service")

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.put("/products/{product_id}")
    def put_product(product_id: str, p: ProductIn):
        if p.product_id != product_id:
            raise HTTPException(422, "product_id in path and body differ")
        guard(inv.upsert_product, p.product_id, p.ean13, p.name)
        return {"product_id": product_id}

    @app.post("/restocks")
    def restock(r: RestockIn):
        return guard(inv.restock, r.store_id, r.ean13, r.qty, r.scan_id)

    @app.get("/stock/{store_id}")
    def stock_levels(store_id: str):
        return inv.stock_levels(store_id)

    @app.get("/stock/{store_id}/{product_id}")
    def stock(store_id: str, product_id: str):
        return {"product_id": product_id, "qty": inv.stock(store_id, product_id),
                "history": inv.history(store_id, product_id, 20)}

    @app.post("/sales", status_code=201)
    def create_sale(s: SaleIn):
        return guard(inv.create_sale, s.sale_id, s.store_id, [Item(i.product_id, i.qty) for i in s.items])

    @app.get("/sales/{sale_id}")
    def get_sale(sale_id: str):
        return guard(inv.sale, sale_id)

    @app.post("/sales/{sale_id}/commit")
    def commit_sale(sale_id: str, body: CommitIn):
        return guard(inv.commit_sale, sale_id, body.payment_ref)

    @app.post("/sales/{sale_id}/cancel")
    def cancel_sale(sale_id: str):
        return guard(inv.cancel_sale, sale_id)

    @app.post("/sales/{sale_id}/returns")
    def return_items(sale_id: str, r: ReturnIn):
        return guard(inv.return_items, sale_id, r.product_id, r.qty, r.return_id)

    @app.post("/adjustments")
    def adjust(a: AdjustIn):
        return guard(inv.adjust, a.store_id, a.product_id, a.qty_change, a.reason, a.ref)

    @app.post("/reconcile")
    def reconcile(s: SnapshotIn):
        return {"differences": guard(sync.reconcile, db, s.store_id, {k: int(v) for k, v in s.stock.items()}, s.tolerance)}

    @app.get("/discrepancies/{store_id}")
    def discrepancies(store_id: str):
        return sync.open_discrepancies(db, store_id)

    @app.post("/discrepancies/{discrepancy_id}/count")
    def resolve(discrepancy_id: int, c: CountIn):
        return guard(sync.resolve_with_count, db, inv, discrepancy_id, c.counted_qty, c.counted_by)

    return app
