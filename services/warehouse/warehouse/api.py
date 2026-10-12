"""HTTP API and phone page for the Warehouse Service.

    uvicorn warehouse.api:create_app --factory --port 8080

Stock controllers use the page at /warehouse/ on their phone (logins made with scripts/staff.sh add <name> --stock).
The proxy passes the signed-in name in X-Staff-User; every change is recorded with it.
"""

import logging
import os
from pathlib import Path
from typing import List, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .db import Database, database_from_env
from .service import Conflict, NotEnoughStock, NotFound, Unavailable, Warehouse

log = logging.getLogger("warehouse")
STATIC = Path(__file__).parent / "static"
EAN = r"^\d{13}$"


class HttpInventory:
    """Products and store restocks, from the Inventory Service."""

    def __init__(self, base_url: str, http: Optional[httpx.Client] = None):
        self.http = http or httpx.Client(base_url=base_url, timeout=10.0)

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError as e:
            log.warning("Inventory unavailable: %s", e)
            raise Unavailable("Can't reach the Inventory Service. Check the connection and try again.")
        if r.status_code >= 500:
            raise Unavailable(f"The Inventory Service had a problem ({r.status_code}). Try again.")
        return r

    def product(self, ean13: str) -> Optional[dict]:
        r = self._request("GET", "/products")
        return next((p for p in r.json() if p["ean13"] == ean13), None)

    def restock(self, store_id: str, ean13: str, qty: int, scan_id: str, recorded_by: str) -> None:
        r = self._request("POST", "/restocks", json={"store_id": store_id, "ean13": ean13, "qty": qty,
                                                     "scan_id": scan_id, "recorded_by": recorded_by})
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise Conflict(f"The store's stock refused {ean13}: {detail}")


class Settings(BaseModel):
    warehouse_id: str = "WH1"
    warehouse_name: str = "Warehouse"
    stores: List[str] = ["001"]  # stores this warehouse supplies
    inventory_url: str = "http://localhost:8010"
    dev_user: str = ""  # without the proxy (development): act as this person


def load_settings() -> Settings:
    return Settings(warehouse_id=os.getenv("WAREHOUSE_ID", "WH1"),
                    warehouse_name=os.getenv("WAREHOUSE_NAME", "Warehouse"),
                    stores=[s.strip() for s in os.getenv("WAREHOUSE_STORES", "001").split(",") if s.strip()],
                    inventory_url=os.getenv("INVENTORY_URL", "http://localhost:8010"),
                    dev_user=os.getenv("DEV_USER", ""))


class ReceiveIn(BaseModel):
    ean13: str = Field(..., pattern=EAN)
    qty: int = Field(..., gt=0, le=100000)
    bin: str = Field(..., max_length=40)
    ref: str = Field("", max_length=60, description="The supplier's delivery note")
    scan_id: str = Field(..., min_length=8, max_length=64, description="Made by the page once per scan")


class CountIn(BaseModel):
    ean13: str = Field(..., pattern=EAN)
    bin: str = Field(..., max_length=40)
    counted_qty: int = Field(..., ge=0, le=100000)
    count_id: str = Field(..., min_length=8, max_length=64)


class MoveIn(BaseModel):
    ean13: str = Field(..., pattern=EAN)
    qty: int = Field(..., gt=0, le=100000)
    from_bin: str = Field(..., max_length=40)
    to_bin: str = Field(..., max_length=40)
    move_id: str = Field(..., min_length=8, max_length=64)


class LineIn(BaseModel):
    ean13: str = Field(..., pattern=EAN)
    bin: str = Field(..., max_length=40)
    qty: int = Field(..., gt=0, le=100000)


class TransferIn(BaseModel):
    transfer_id: str = Field(..., min_length=8, max_length=64)
    store_id: str = Field(..., min_length=1, max_length=20)
    lines: List[LineIn] = Field(..., min_length=1, max_length=200)


def create_app(settings: Optional[Settings] = None, db: Optional[Database] = None, inventory=None) -> FastAPI:
    settings = settings or load_settings()
    db = db or database_from_env()
    inventory = inventory or HttpInventory(settings.inventory_url)
    wh = Warehouse(db, inventory, settings.warehouse_id)
    app = FastAPI(title="Smart Shopping - Warehouse Service")

    def who(user: Optional[str] = Header(None, alias="X-Staff-User", include_in_schema=False)) -> str:
        name = user or settings.dev_user
        if not name:
            raise HTTPException(401, "Sign in with your stock controller login.")
        return name

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except NotEnoughStock as e:
            raise HTTPException(409, {"message": str(e), "short": e.short})
        except Conflict as e:
            raise HTTPException(409, str(e))
        except Unavailable as e:
            raise HTTPException(503, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/me")
    def me(name: str = Depends(who),
           role: Optional[str] = Header(None, alias="X-Staff-Role", include_in_schema=False)):
        return {"name": name, "role": role or "", "warehouse_id": settings.warehouse_id,
                "warehouse_name": settings.warehouse_name, "stores": settings.stores}

    @app.get("/api/products/{ean13}")
    def product(ean13: str, _: str = Depends(who)):
        p = guard(wh.product, ean13)
        return {**p, "bins": wh.stock(ean13=ean13)}

    @app.get("/api/stock")
    def stock(bin: Optional[str] = Query(None, max_length=40), _: str = Depends(who)):
        return guard(wh.stock, bin=bin)

    @app.get("/api/history")
    def history(limit: int = Query(50, ge=1, le=500), _: str = Depends(who)):
        return wh.history(limit)

    @app.post("/api/receipts")
    def receive(body: ReceiveIn, name: str = Depends(who)):
        return guard(wh.receive, body.ean13, body.qty, body.bin, body.ref, body.scan_id, name)

    @app.get("/api/counts")
    def counts(limit: int = Query(50, ge=1, le=500), _: str = Depends(who)):
        return wh.counts(limit)

    @app.post("/api/counts")
    def count(body: CountIn, name: str = Depends(who)):
        return guard(wh.count, body.ean13, body.bin, body.counted_qty, body.count_id, name)

    @app.post("/api/moves")
    def move(body: MoveIn, name: str = Depends(who)):
        return guard(wh.move, body.ean13, body.qty, body.from_bin, body.to_bin, body.move_id, name)

    @app.get("/api/transfers")
    def transfers(status: Optional[Literal["dispatched", "received"]] = None,
                  limit: int = Query(50, ge=1, le=500), _: str = Depends(who)):
        return wh.transfers(status, limit)

    @app.get("/api/transfers/{transfer_id}")
    def transfer(transfer_id: str, _: str = Depends(who)):
        return guard(wh.transfer, transfer_id)

    @app.post("/api/transfers", status_code=201)
    def send(body: TransferIn, name: str = Depends(who)):
        if body.store_id not in settings.stores:
            raise HTTPException(422, f"This warehouse supplies stores {', '.join(settings.stores)}")
        return guard(wh.send, body.transfer_id, body.store_id, [(l.ean13, l.bin, l.qty) for l in body.lines], name)

    @app.post("/api/transfers/{transfer_id}/received")
    def received(transfer_id: str, name: str = Depends(who)):
        return guard(wh.receive_at_store, transfer_id, name)

    return app
