"""Rider app: the store's riders see their deliveries on their phone.

    uvicorn rider.app:create_app --factory --port 8060

Riders sign in with their own login (scripts/staff.sh add <name> --rider); the proxy passes the name in
X-Staff-User. A rider:
- starts and ends their shift, and saves the phone number customers see;
- accepts a waiting delivery, or gets one assigned by the store;
- collects the parcel at the counter (staff record the handover on the dashboard);
- finishes by typing the customer's 4-digit delivery code, or reports a problem.

Customer details (name, phone, exact address) are shown only once the job is the rider's own. The
delivery code is never sent to this app: the Dispatch Service checks it.
"""

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

log = logging.getLogger("rider")
STATIC = Path(__file__).parent / "static"


class Settings(BaseModel):
    store_id: str = "001"
    store_name: str = "Smart Shopping"
    dispatch_url: str = "http://localhost:8050"
    inventory_url: str = "http://localhost:8010"
    city: str = "Nairobi"  # added to map searches for deliveries without a location pin
    dev_rider: str = ""  # without the proxy (development): act as this rider


def load_settings() -> Settings:
    return Settings(store_id=os.getenv("STORE_ID", "001"), store_name=os.getenv("STORE_NAME", "Smart Shopping"),
                    dispatch_url=os.getenv("DISPATCH_URL", "http://localhost:8050"),
                    inventory_url=os.getenv("INVENTORY_URL", "http://localhost:8010"),
                    city=os.getenv("STORE_CITY", "Nairobi"), dev_rider=os.getenv("DEV_RIDER", ""))


class MeIn(BaseModel):
    phone: Optional[str] = Field(None, max_length=20)
    on_shift: Optional[bool] = None


class PinIn(BaseModel):
    pin: str = Field(..., min_length=1, max_length=8)


class ProblemIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)


def maps_link(d: dict, city: str = "Nairobi") -> str:
    """Directions to the customer's pin, or a search for their address."""
    if d.get("lat") is not None and d.get("lng") is not None:
        return f"https://www.google.com/maps/dir/?api=1&destination={d['lat']},{d['lng']}"
    return "https://www.google.com/maps/search/?api=1&query=" + quote(", ".join(filter(None, [d["address"], d["area"], city])))


def create_app(settings: Optional[Settings] = None, dispatch: Optional[httpx.Client] = None,
               inventory: Optional[httpx.Client] = None) -> FastAPI:
    settings = settings or load_settings()
    dispatch = dispatch or httpx.Client(base_url=settings.dispatch_url, timeout=10.0)
    inventory = inventory or httpx.Client(base_url=settings.inventory_url, timeout=10.0)
    app = FastAPI(title="Smart Shopping - Rider app")

    def rider_name(user: Optional[str] = Header(None, alias="X-Staff-User", include_in_schema=False)) -> str:
        name = user or settings.dev_rider
        if not name:
            raise HTTPException(401, "Sign in with your rider login.")
        return name

    def call(method: str, path: str, **kw):
        try:
            r = dispatch.request(method, path, **kw)
        except httpx.HTTPError as e:
            log.error("Dispatch unavailable: %s", e)
            raise HTTPException(503, "Can't reach the store right now. Check your connection and try again.")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise HTTPException(r.status_code, detail)
        return r.json()

    def items_of(sale_id: str) -> Optional[int]:
        try:
            r = inventory.get(f"/sales/{sale_id}")
            return sum(it["qty"] for it in r.json()["items"]) if r.status_code == 200 else None
        except (httpx.HTTPError, KeyError, ValueError):
            return None

    def mine(d: dict) -> dict:
        return {"sale_id": d["sale_id"], "code": d["sale_id"][-8:], "status": d["status"], "area": d["area"],
                "address": d["address"], "notes": d["notes"], "customer_name": d["customer_name"],
                "customer_phone": d["customer_phone"], "maps": maps_link(d, settings.city), "items": items_of(d["sale_id"]),
                "fee": d["fee_kes"], "locked": d["locked"], "fail_reason": d.get("fail_reason", "")}

    def waiting(d: dict) -> dict:
        """A job nobody has taken: enough to decide, not the customer's details."""
        return {"sale_id": d["sale_id"], "code": d["sale_id"][-8:], "area": d["area"], "fee": d["fee_kes"],
                "created_at": d["created_at"]}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/me")
    def me(name: str = Depends(rider_name)):
        try:
            rider = call("GET", f"/riders/{name}")
        except HTTPException as e:
            if e.status_code != 404:
                raise
            rider = call("PUT", f"/riders/{name}", json={})  # first sign-in
        return {**rider, "store_name": settings.store_name}

    @app.put("/api/me")
    def update_me(body: MeIn, name: str = Depends(rider_name)):
        return call("PUT", f"/riders/{name}", json=body.model_dump(exclude_none=True))

    @app.get("/api/jobs")
    def jobs(name: str = Depends(rider_name)):
        rider = me(name)
        own = call("GET", "/deliveries", params={"store_id": settings.store_id, "rider_id": name,
                                                 "status": "assigned,picked_up,failed"})
        free = call("GET", "/deliveries", params={"store_id": settings.store_id, "status": "unassigned"}) \
            if rider["on_shift"] else []
        return {"mine": [mine(d) for d in own], "available": [waiting(d) for d in free]}

    @app.post("/api/jobs/{sale_id}/accept")
    def accept(sale_id: str, name: str = Depends(rider_name)):
        return mine(call("POST", f"/deliveries/{sale_id}/claim", json={"rider_id": name}))

    @app.post("/api/jobs/{sale_id}/release")
    def release(sale_id: str, name: str = Depends(rider_name)):
        call("POST", f"/deliveries/{sale_id}/release", json={"by": name, "rider_id": name})
        return {"released": sale_id}

    @app.post("/api/jobs/{sale_id}/delivered")
    def delivered(sale_id: str, body: PinIn, name: str = Depends(rider_name)):
        try:
            return mine(call("POST", f"/deliveries/{sale_id}/delivered", json={"rider_id": name, "pin": body.pin}))
        except HTTPException as e:
            if e.status_code == 422 and isinstance(e.detail, dict):
                raise HTTPException(422, e.detail["message"])
            raise

    @app.post("/api/jobs/{sale_id}/problem")
    def problem(sale_id: str, body: ProblemIn, name: str = Depends(rider_name)):
        return mine(call("POST", f"/deliveries/{sale_id}/failed", json={"rider_id": name, "reason": body.reason}))

    return app
