"""Loyalty points in Scan & Go: customers with an account (made in the online shop) sign in here with an emailed
code, earn points on what they pay for, and can spend them at checkout. The Customers Service keeps the accounts
and the points; this keeps the session cookie.

The cookie (HttpOnly, SameSite=Lax, Secure over HTTPS) is Scan & Go's own, scoped to its path: signing in here
doesn't sign anyone in to the online shop, or the other way round. The Customers Service stores only a hash of the
token. Code requests are rate limited per address and per network.
"""

import logging
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import i18n

log = logging.getLogger("scan_and_go.account")
COOKIE = "sg_session"


class EmailIn(BaseModel):
    email: str = Field(..., max_length=120)


class VerifyIn(EmailIn):
    code: str = Field(..., max_length=12)


class Accounts:
    def __init__(self, http: Optional[httpx.Client]):
        self.http = http
        self._settings, self._settings_at = None, 0.0

    @property
    def on(self) -> bool:
        return self.http is not None

    def call(self, method: str, path: str, token: str = "", **kw) -> httpx.Response:
        if not self.on:
            raise HTTPException(404, "Points aren't available in this store")
        headers = kw.pop("headers", {}) or {}
        if token:
            headers["X-Session"] = token
        try:
            return self.http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as e:
            log.error("Customers Service unavailable: %s", e)
            raise HTTPException(503, "Points aren't available right now. You can still pay without them.")

    def settings(self) -> Optional[dict]:
        """Loyalty settings (cached a minute); None when the service is off or down."""
        if not self.on:
            return None
        if self._settings is None or time.monotonic() - self._settings_at > 60:
            try:
                r = self.http.get("/settings")
                r.raise_for_status()
                self._settings, self._settings_at = r.json(), time.monotonic()
            except httpx.HTTPError as e:
                log.warning("Customers Service unavailable: %s", e)
        return self._settings

    def loyalty_on(self) -> bool:
        s = self.settings()
        return bool(s and s.get("loyalty_enabled"))

    def current(self, request: Request) -> Optional[dict]:
        token = request.cookies.get(COOKIE)
        if not token or not self.on:
            return None
        try:
            r = self.http.get("/me", headers={"X-Session": token})
        except httpx.HTTPError as e:
            log.warning("Customers Service unavailable: %s", e)
            return None
        return r.json() if r.status_code == 200 else None

    def start_order(self, request: Request, sale_id: str, goods_kes: int, use_points: bool) -> Optional[dict]:
        """Link the sale to the signed-in customer, spending points if asked: {discount_kes, customer_id, ...} or None.
        Not signed in, or the service down: the sale goes ahead without points (unless they asked to spend some)."""
        token = request.cookies.get(COOKIE)
        if not token or not self.on or not self.loyalty_on():
            if use_points:
                raise HTTPException(401, "Sign in to use your points.")
            return None
        try:
            r = self.http.post("/orders", json={"sale_id": sale_id, "goods_kes": goods_kes, "use_points": use_points},
                               headers={"X-Session": token})
        except httpx.HTTPError as e:
            log.error("Customers Service unavailable for %s: %s", sale_id, e)
            r = None
        if r is not None and r.status_code == 201:
            return r.json()
        if use_points:
            raise HTTPException(503, "Your points can't be used right now. Try again, or pay without them.")
        return None

    def cancel_order(self, sale_id: str) -> None:
        """The sale didn't go ahead: give back any points spent on it."""
        if not self.on:
            return
        try:
            self.http.post(f"/orders/{sale_id}/cancel")
        except httpx.HTTPError as e:  # the Customers Service gives them back anyway once the sale shows as cancelled
            log.warning("Couldn't give points back for %s yet: %s", sale_id, e)

    def points_for(self, sale: dict) -> Optional[dict]:
        """For a signed-in customer's sale: points spent on it, and earned (or to be earned once paid)."""
        s = self.settings() if sale.get("customer_id") else None
        if not s or not s.get("loyalty_enabled"):
            return None
        goods = sum((it.get("unit_price_kes") or 0) * it["qty"] for it in sale.get("items", []))
        off = sale.get("loyalty_discount_kes") or 0
        return {"earn": max(0, goods - off) // s["kes_per_point"], "discount": off,
                "spent": off // s["redeem_kes"] * s["redeem_points"]}


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail", "")
    except ValueError:
        d = ""
    if isinstance(d, dict):
        d = d.get("message", "")
    if isinstance(d, list):
        d = "Please check what you entered."
    return d or "Something went wrong. Please try again."


def install(app: FastAPI, accounts: Accounts, limiter) -> None:
    """The /api/account routes: who's signed in and their points, a sign-in code, check it, sign out."""

    def client_key(request: Request) -> str:
        return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")

    def limit(request: Request, email: str = "") -> None:
        if not limiter.allow("net:" + client_key(request)) or (email and not limiter.allow("email:" + email.strip().lower())):
            raise HTTPException(429, "Too many tries. Please wait a few minutes.")

    def set_cookie(request: Request, response: Response, value: str, max_age: int) -> None:
        response.set_cookie(COOKIE, value, max_age=max_age, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", path=(request.scope.get("root_path") or "") + "/")

    @app.get("/api/account")
    def me(request: Request):
        """Whether points are on here, and the signed-in customer's name and points (nothing more is shown here)."""
        if not accounts.on or not accounts.loyalty_on():
            return {"available": False, "signed_in": False}
        c = accounts.current(request)
        return {"available": True, "signed_in": bool(c), "settings": accounts.settings(),
                "customer": {"name": c["name"], "points": c["loyalty"]["points"], "worth_kes": c["loyalty"].get("worth_kes", 0),
                             "language": c.get("language", "en")} if c else None}

    @app.post("/api/account/code", status_code=202)
    def send_code(b: EmailIn, request: Request):
        limit(request, b.email)
        r = accounts.call("POST", "/codes", json={**b.model_dump(), "language": i18n.language(request)})
        if r.status_code != 202:
            raise HTTPException(r.status_code if r.status_code < 500 else 502, _detail(r))
        return r.json()

    @app.post("/api/account/verify")
    def verify(b: VerifyIn, request: Request):
        limit(request)
        r = accounts.call("POST", "/sessions", json=b.model_dump())
        if r.status_code != 201:
            raise HTTPException(r.status_code if r.status_code < 500 else 502, _detail(r))
        s = r.json()
        c = s["customer"]
        out = JSONResponse({"signed_in": True, "customer": {"name": c["name"], "points": c["loyalty"]["points"],
                                                            "worth_kes": c["loyalty"].get("worth_kes", 0),
                                                            "language": c.get("language", "en")}})
        set_cookie(request, out, s["token"], 30 * 24 * 3600)
        return out

    @app.post("/api/account/signout")
    def sign_out(request: Request):
        t = request.cookies.get(COOKIE)
        if t and accounts.on:
            try:
                accounts.call("DELETE", "/sessions", t)
            except HTTPException:
                pass
        out = JSONResponse({"signed_in": False})
        set_cookie(request, out, "", 0)
        return out
