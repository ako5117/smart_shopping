"""HTTP API for the Customers Service (accounts and loyalty points). Internal: only the online shop and the
dashboard call it. The shop passes the signed-in customer's session token in X-Session.

    uvicorn customers.api:create_app --factory --port 8090

Settings (environment):
    LOYALTY_ENABLED        true (default) | false: run the points programme or not
    LOYALTY_KES_PER_POINT  100: 1 point per KES 100 spent
    LOYALTY_REDEEM_POINTS  100: points are spent in blocks of this many...
    LOYALTY_REDEEM_KES     50: ...each worth this much off an order
    EMAIL_PROVIDER         outbox (default): the stand-in; emails are kept and shown on the dashboard
    STORE_NAME, INVENTORY_URL, CUSTOMERS_POLL_S (how often paid orders are checked, default 15)
"""

import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Literal, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .db import Database, database_from_env
from .mailer import mailer_from_env
from .photos import BadImage
from .reports import Reports
from .service import Conflict, Customers, NotFound, Settings, TooMany, WrongCode

log = logging.getLogger("customers")


class HttpInventory:
    def __init__(self, base_url: str):
        self.http = httpx.Client(base_url=base_url, timeout=5.0)

    def sale(self, sale_id: str) -> Optional[dict]:
        r = self.http.get(f"/sales/{sale_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


def _flag(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return default
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} should be true or false (it's {v!r})")


def settings_from_env() -> Settings:
    env = os.getenv
    return Settings(store_name=env("STORE_NAME") or "Smart Shopping", loyalty_enabled=_flag("LOYALTY_ENABLED", True),
                    kes_per_point=int(env("LOYALTY_KES_PER_POINT") or 100),
                    redeem_points=int(env("LOYALTY_REDEEM_POINTS") or 100),
                    redeem_kes=int(env("LOYALTY_REDEEM_KES") or 50))


class SignupIn(BaseModel):
    name: str = Field(..., max_length=60)
    phone: str = Field(..., max_length=16)
    email: str = Field(..., max_length=120)
    dob: str = Field(..., max_length=10, description="YYYY-MM-DD")
    gender: Optional[Literal["female", "male", "other", ""]] = None
    consent: bool = False


class EmailIn(BaseModel):
    email: str = Field(..., max_length=120)


class VerifyIn(EmailIn):
    code: str = Field(..., max_length=12)


class ProfileIn(BaseModel):
    name: str = Field(..., max_length=60)
    phone: str = Field(..., max_length=16)
    dob: str = Field(..., max_length=10)
    gender: Optional[Literal["female", "male", "other", ""]] = None


class ReportIn(BaseModel):
    kind: Literal["order", "store", "app", "idea"]
    message: str = Field(..., max_length=2000)
    app: Literal["store", "scan_and_go"] = "store"
    order_id: str = Field("", max_length=40)
    contact: str = Field("", max_length=120, description="Phone or email to reach them, if they want a reply")
    context: dict = Field(default_factory=dict, description="App problems: page, browser, recent errors")
    photo_b64: Optional[str] = Field(None, max_length=8_000_000, description="A photo or screenshot, base64 (JPEG/PNG/WebP)")


class ReportUpdateIn(BaseModel):
    by: str = Field("", max_length=60)
    status: Optional[Literal["open", "in_progress", "resolved"]] = None
    reply: Optional[str] = Field(None, max_length=1000)


class OrderIn(BaseModel):
    sale_id: str = Field(..., min_length=1, max_length=40)
    goods_kes: int = Field(..., ge=0, description="The order's goods total before points, for the discount cap")
    use_points: bool = False


def create_app(db: Optional[Database] = None, inventory=None, settings: Optional[Settings] = None, mailer=None,
               clock=None, start_worker: bool = True) -> FastAPI:
    db = db or database_from_env()
    settings = settings or settings_from_env()
    mailer = mailer or mailer_from_env(db, os.getenv("EMAIL_PROVIDER", "outbox"))
    if inventory is None and os.getenv("INVENTORY_URL"):
        inventory = HttpInventory(os.environ["INVENTORY_URL"])
    customers = Customers(db, mailer, settings, inventory, clock)
    reports = Reports(db)
    poll = float(os.getenv("CUSTOMERS_POLL_S", "15"))

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        worker = None
        if inventory is not None and start_worker:
            def run():
                while not stop.wait(poll):
                    try:
                        customers.settle()
                    except Exception:
                        log.exception("Settling orders failed")
            worker = threading.Thread(target=run, name="customers-settle", daemon=True)
            worker.start()
        log.info("Loyalty points %s; email: %s", "on" if settings.loyalty_enabled else "off", mailer.name)
        yield
        stop.set()
        if worker:
            worker.join(timeout=poll + 5)

    app = FastAPI(title="Smart Shopping - Customers Service", lifespan=lifespan)
    app.state.customers = customers

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except WrongCode as e:
            raise HTTPException(422, {"message": str(e), "attempts_left": e.attempts_left})
        except (ValueError, BadImage) as e:
            raise HTTPException(422, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))
        except TooMany as e:
            raise HTTPException(429, str(e))

    def me(token: Optional[str]) -> str:
        return guard(customers.customer_for, token or "")

    @app.get("/health")
    def health():
        return {"status": "ok", "email": mailer.name, "loyalty": settings.loyalty_enabled}

    @app.get("/settings")
    def public_settings():
        return {**settings.public(), "email_provider": mailer.name}

    @app.post("/signup", status_code=202)
    def signup(b: SignupIn):
        return guard(customers.signup, b.name, b.phone, b.email, b.dob, b.gender, b.consent)

    @app.post("/codes", status_code=202)
    def send_code(b: EmailIn):
        return guard(customers.request_code, b.email)

    @app.post("/sessions", status_code=201)
    def verify(b: VerifyIn):
        return guard(customers.verify, b.email, b.code)

    @app.delete("/sessions", status_code=204)
    def sign_out(x_session: Optional[str] = Header(None)):
        customers.sign_out(x_session or "")

    @app.get("/me")
    def profile(x_session: Optional[str] = Header(None)):
        cid = me(x_session)
        customers.settle(cid)  # so points from a just-paid order show straight away
        return customers.profile(cid)

    @app.put("/me")
    def update(b: ProfileIn, x_session: Optional[str] = Header(None)):
        return guard(customers.update, me(x_session), b.name, b.phone, b.dob, b.gender)

    @app.delete("/me", status_code=204)
    def delete(x_session: Optional[str] = Header(None)):
        customers.delete(me(x_session))

    @app.put("/me/photo")
    async def put_photo(request: Request, x_session: Optional[str] = Header(None)):
        cid = me(x_session)
        return guard(customers.set_photo, cid, await request.body())

    @app.get("/me/photo")
    def get_photo(x_session: Optional[str] = Header(None)):
        return Response(guard(customers.photo, me(x_session)), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})

    @app.delete("/me/photo")
    def delete_photo(x_session: Optional[str] = Header(None)):
        return customers.delete_photo(me(x_session))

    @app.get("/me/orders")
    def my_orders(x_session: Optional[str] = Header(None)):
        return customers.orders(me(x_session))

    @app.post("/orders", status_code=201)
    def start_order(b: OrderIn, x_session: Optional[str] = Header(None)):
        return guard(customers.start_order, me(x_session), b.sale_id, b.goods_kes, b.use_points)

    @app.post("/orders/{sale_id}/cancel")
    def cancel_order(sale_id: str):
        return guard(customers.cancel_order, sale_id)

    # problem reports
    @app.post("/reports", status_code=201)
    def report(b: ReportIn, x_session: Optional[str] = Header(None)):
        cid = None
        if x_session:
            try:
                cid = customers.customer_for(x_session)
            except NotFound:
                cid = None  # signed out meanwhile: the report still counts
        photo = None
        if b.photo_b64:
            import base64
            import binascii
            try:
                photo = base64.b64decode(b.photo_b64.split(",", 1)[-1], validate=True)
            except (binascii.Error, ValueError):
                raise HTTPException(422, "That picture couldn't be read")
        return guard(reports.create, b.kind, b.message, b.app, b.order_id, b.contact, b.context, cid, photo)

    @app.get("/reports")
    def list_reports(status: Optional[str] = Query(None, description="Comma-separated; default open,in_progress"),
                     limit: int = Query(100, ge=1, le=500)):
        return {"reports": guard(reports.list, status.split(",") if status else None, limit), "counts": reports.counts()}

    @app.get("/reports/{report_id}")
    def get_report(report_id: str):
        return guard(reports.get, report_id)

    @app.get("/reports/{report_id}/public")
    def public_report(report_id: str):
        return guard(reports.public, report_id)

    @app.get("/reports/{report_id}/photo")
    def report_photo(report_id: str):
        return Response(guard(reports.photo, report_id), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/reports/{report_id}")
    def update_report(report_id: str, b: ReportUpdateIn):
        return guard(reports.update, report_id, b.by, b.status, b.reply)

    @app.get("/me/reports")
    def my_reports(x_session: Optional[str] = Header(None)):
        return reports.for_customer(me(x_session))

    # dashboard
    @app.get("/summary")
    def summary():
        return customers.summary()

    @app.get("/members")
    def members(limit: int = Query(50, ge=1, le=500)):
        return customers.members(limit)

    @app.get("/emails")
    def emails(limit: int = Query(30, ge=1, le=200)):
        return customers.emails(limit)

    return app
