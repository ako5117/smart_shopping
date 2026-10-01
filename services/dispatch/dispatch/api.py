"""HTTP API for the Dispatch Service.

    uvicorn dispatch.api:create_app --factory --port 8050

Only other services call it (the online shop, the store dashboard and the rider app), inside Docker's
network; through the proxy it is open to managers only, like the other service APIs. The delivery code is
returned only to the online shop, for the customer (GET /deliveries/{sale_id}?include_pin=true).
"""

import logging
import os
from typing import List, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .db import Database, database_from_env
from .service import DEFAULT_AREAS, Conflict, Dispatch, Locked, NotFound, WrongPin, parse_areas

log = logging.getLogger("dispatch")


class HttpInventory:
    def __init__(self, base_url: str, http: Optional[httpx.Client] = None):
        self.http = http or httpx.Client(base_url=base_url, timeout=5.0)

    def get_sale(self, sale_id: str) -> Optional[dict]:
        try:
            r = self.http.get(f"/sales/{sale_id}")
        except httpx.HTTPError as e:
            log.warning("Inventory unavailable checking %s: %s", sale_id, e)
            return None
        return r.json() if r.status_code == 200 else None

    def record_exit(self, sale_id: str, checked_by: str) -> Tuple[bool, str]:
        try:
            r = self.http.post(f"/sales/{sale_id}/exit", json={"checked_by": checked_by})
        except httpx.HTTPError as e:
            return False, f"The Inventory Service is unavailable ({e}). Try again."
        if r.status_code == 200:
            return True, ""
        try:
            return False, r.json().get("detail", r.text)
        except ValueError:
            return False, r.text


class DeliveryIn(BaseModel):
    sale_id: str = Field(..., min_length=1, max_length=64)
    store_id: str = Field(..., min_length=1, max_length=20)
    customer_name: str = Field(..., min_length=1, max_length=60)
    customer_phone: str = Field(..., min_length=9, max_length=16)
    area: str = Field(..., min_length=1, max_length=60)
    address: str = Field(..., min_length=3, max_length=200)
    notes: str = Field("", max_length=300)
    lat: Optional[float] = None
    lng: Optional[float] = None


class RiderIn(BaseModel):
    phone: Optional[str] = Field(None, max_length=20)
    on_shift: Optional[bool] = None


class ByIn(BaseModel):
    by: str = Field(..., min_length=1, max_length=60, description="Who did it")


class AssignIn(ByIn):
    rider_id: str = Field(..., min_length=1, max_length=60)


class RiderActionIn(BaseModel):
    rider_id: str = Field(..., min_length=1, max_length=60)


class DeliverIn(RiderActionIn):
    pin: str = Field(..., min_length=1, max_length=8)


class FailIn(RiderActionIn):
    reason: str = Field(..., max_length=300)


class ReleaseIn(ByIn):
    rider_id: Optional[str] = Field(None, description="Set when the rider gives the job back themselves")


def create_app(db: Optional[Database] = None, inventory=None, areas: Optional[dict] = None) -> FastAPI:
    db = db or database_from_env()
    inventory = inventory or HttpInventory(os.getenv("INVENTORY_URL", "http://localhost:8010"))
    areas = areas or parse_areas(os.getenv("DELIVERY_AREAS") or DEFAULT_AREAS)
    dispatch = Dispatch(db, inventory, areas)
    app = FastAPI(title="Smart Shopping - Dispatch Service")

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except WrongPin as e:
            raise HTTPException(422, {"message": str(e), "attempts_left": e.attempts_left})
        except Locked as e:
            raise HTTPException(423, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/areas")
    def list_areas():
        """Where the store delivers, and the fee for each area."""
        return [{"area": a, "fee_kes": fee} for a, fee in areas.items()]

    @app.post("/deliveries", status_code=201)
    def create(d: DeliveryIn):
        return guard(dispatch.create, d.sale_id, d.store_id, d.customer_name, d.customer_phone, d.area,
                     d.address, d.notes, d.lat, d.lng)

    @app.get("/deliveries")
    def list_deliveries(store_id: str, status: Optional[str] = Query(None, description="Comma-separated; default: active"),
                        rider_id: Optional[str] = None, limit: int = Query(200, ge=1, le=500)):
        statuses: Optional[List[str]] = [s for s in status.split(",") if s] if status else None
        return guard(dispatch.list, store_id, statuses, rider_id, limit)

    @app.get("/deliveries/{sale_id}")
    def get(sale_id: str, include_pin: bool = False):
        return guard(dispatch.get, sale_id, include_pin)

    @app.get("/deliveries/{sale_id}/events")
    def events(sale_id: str):
        guard(dispatch.get, sale_id)
        return dispatch.events(sale_id)

    @app.post("/deliveries/{sale_id}/assign")
    def assign(sale_id: str, body: AssignIn):
        return guard(dispatch.assign, sale_id, body.rider_id, body.by)

    @app.post("/deliveries/{sale_id}/claim")
    def claim(sale_id: str, body: RiderActionIn):
        return guard(dispatch.claim, sale_id, body.rider_id)

    @app.post("/deliveries/{sale_id}/release")
    def release(sale_id: str, body: ReleaseIn):
        return guard(dispatch.release, sale_id, body.by, body.rider_id)

    @app.post("/deliveries/{sale_id}/hand-over")
    def hand_over(sale_id: str, body: ByIn):
        return guard(dispatch.hand_over, sale_id, body.by)

    @app.post("/deliveries/{sale_id}/delivered")
    def delivered(sale_id: str, body: DeliverIn):
        return guard(dispatch.deliver, sale_id, body.rider_id, body.pin)

    @app.post("/deliveries/{sale_id}/failed")
    def failed(sale_id: str, body: FailIn):
        return guard(dispatch.fail, sale_id, body.rider_id, body.reason)

    @app.post("/deliveries/{sale_id}/retry")
    def retry(sale_id: str, body: ByIn):
        return guard(dispatch.retry, sale_id, body.by)

    @app.get("/riders")
    def riders():
        return dispatch.riders()

    @app.get("/riders/{rider_id}")
    def rider(rider_id: str):
        return guard(dispatch.rider, rider_id)

    @app.put("/riders/{rider_id}")
    def save_rider(rider_id: str, body: RiderIn):
        if len(rider_id) > 60:
            raise HTTPException(422, "Rider name too long")
        return guard(dispatch.save_rider, rider_id, body.phone, body.on_shift)

    return app
