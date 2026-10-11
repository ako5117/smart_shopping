"""Report a problem: customers tell the store about an order, something in the store, the app, or an idea.
The Customers Service keeps the reports; staff answer them on the dashboard's Reports page."""

import re
import time
from collections import defaultdict, deque
from typing import Dict, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .account import COOKIE, Accounts, _detail

REPORT_ID = re.compile(r"^R-[0-9A-F]{10}$")


class ReportIn(BaseModel):
    kind: Literal["order", "store", "app", "idea"]
    message: str = Field(..., min_length=5, max_length=2000)
    order_id: str = Field("", max_length=40)
    contact: str = Field("", max_length=120)
    context: Dict[str, str] = Field(default_factory=dict)
    photo_b64: Optional[str] = Field(None, max_length=8_000_000)


class _Window:
    def __init__(self, limit: int, seconds: int):
        self.limit, self.seconds, self.hits = limit, seconds, defaultdict(deque)

    def allow(self, key: str) -> bool:
        now, q = time.monotonic(), self.hits[key]
        while q and now - q[0] > self.seconds:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


def install(app: FastAPI, accounts: Accounts, app_name: str = "store") -> None:
    window = _Window(6, 3600)

    @app.post("/api/reports", status_code=201)
    def report(body: ReportIn, request: Request):
        """Send a report, signed in or not. The answer has the reference to follow it with."""
        who = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
        if not window.allow(who):
            raise HTTPException(429, "Thanks: we've had several reports from you in the last hour. Please ask at the store if it's urgent.")
        context = {}
        if body.kind == "app":  # only app problems carry the page and browser, to help fix them
            context = {k[:40]: v[:500] for k, v in list(body.context.items())[:12]}
            context["user_agent"] = request.headers.get("user-agent", "")[:300]
        r = accounts.call("POST", "/reports", request.cookies.get(COOKIE, ""),
                          json={**body.model_dump(), "app": app_name, "context": context})
        if r.status_code != 201:
            raise HTTPException(r.status_code if r.status_code < 500 else 502, _detail(r))
        out = r.json()
        return {k: out[k] for k in ("report_id", "kind", "kind_label", "status", "created_at")}

    @app.get("/api/reports/{report_id}")
    def follow(report_id: str):
        """A report's status and the store's reply, for whoever has its reference."""
        if not REPORT_ID.match(report_id):
            raise HTTPException(404, "No such report")
        r = accounts.call("GET", f"/reports/{report_id}/public")
        if r.status_code != 200:
            raise HTTPException(404, "No such report")
        return r.json()

    @app.get("/api/account/reports")
    def mine(request: Request):
        t = request.cookies.get(COOKIE)
        if not t:
            raise HTTPException(401, "Please sign in.")
        r = accounts.call("GET", "/me/reports", t)
        if r.status_code != 200:
            raise HTTPException(401, "Please sign in again.")
        return r.json()
