"""Customer accounts in the online shop: sign up, sign in with an emailed code, profile and photo, past
orders, and loyalty points. The Customers Service keeps the accounts; this keeps the session cookie.

The cookie (HttpOnly, SameSite=Lax, Secure over HTTPS) holds a random session token; the Customers
Service stores only its hash. Sign-up and code requests are rate limited per address and per network.
"""

import logging
import time
from typing import Callable, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

log = logging.getLogger("storefront.account")
COOKIE = "ss_session"
MAX_PHOTO = 12 * 1024 * 1024


class SignupIn(BaseModel):
    name: str = Field(..., max_length=60)
    phone: str = Field(..., max_length=16)
    email: str = Field(..., max_length=120)
    dob: str = Field(..., max_length=10)
    gender: Optional[str] = Field(None, max_length=10)
    consent: bool = False


class EmailIn(BaseModel):
    email: str = Field(..., max_length=120)


class VerifyIn(EmailIn):
    code: str = Field(..., max_length=12)


class ProfileIn(BaseModel):
    name: str = Field(..., max_length=60)
    phone: str = Field(..., max_length=16)
    dob: str = Field(..., max_length=10)
    gender: Optional[str] = Field(None, max_length=10)


class Accounts:
    """What the rest of the shop needs: who's signed in, and their points on an order."""

    def __init__(self, http: Optional[httpx.Client]):
        self.http = http
        self._settings, self._settings_at = None, 0.0

    @property
    def on(self) -> bool:
        return self.http is not None

    def call(self, method: str, path: str, token: str = "", **kw) -> httpx.Response:
        if not self.on:
            raise HTTPException(404, "Accounts aren't available in this shop")
        headers = kw.pop("headers", {}) or {}
        if token:
            headers["X-Session"] = token
        try:
            return self.http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as e:
            log.error("Customers Service unavailable: %s", e)
            raise HTTPException(503, "Accounts aren't available right now. You can still shop without signing in.")

    def settings(self) -> Optional[dict]:
        """Loyalty settings (cached a minute); None when accounts are off or the service is down."""
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
        return self._settings

    def current(self, request: Request) -> Optional[dict]:
        """The signed-in customer's profile, or None (not signed in, or accounts unavailable)."""
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
        """Link the order to the signed-in customer and spend points if asked: {discount_kes, ...} or None."""
        token = request.cookies.get(COOKIE)
        if not token or not self.on:
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
            raise HTTPException(503, "Your points can't be used right now. Try again, or order without them.")
        return None  # the order still goes ahead; it just won't earn points

    def cancel_order(self, sale_id: str) -> None:
        if not self.on:
            return
        try:
            self.http.post(f"/orders/{sale_id}/cancel")
        except httpx.HTTPError as e:  # the Customers Service cancels it anyway once the sale shows as cancelled
            log.warning("Couldn't give points back for %s yet: %s", sale_id, e)


def _detail(r: httpx.Response):
    try:
        d = r.json().get("detail", "")
    except ValueError:
        d = ""
    if isinstance(d, list):
        d = "Please check what you entered."
    return d or "Something went wrong. Please try again."


def _pass(r: httpx.Response, ok=(200, 201, 202)):
    if r.status_code in ok:
        return r.json()
    if r.status_code == 404:
        raise HTTPException(401, "Please sign in again.")
    raise HTTPException(r.status_code if r.status_code < 500 else 502, _detail(r))


def install(app: FastAPI, accounts: Accounts, limiter, sale_lines: Callable[[str], Optional[dict]]) -> None:
    """The /api/account routes. sale_lines(sale_id) gives an order's items, total and status for the history."""

    def client_key(request: Request) -> str:
        return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")

    def limit(request: Request, email: str = "") -> None:
        if not limiter.allow("net:" + client_key(request)) or (email and not limiter.allow("email:" + email.strip().lower())):
            raise HTTPException(429, "Too many tries. Please wait a few minutes.")

    def token(request: Request) -> str:
        t = request.cookies.get(COOKIE)
        if not t:
            raise HTTPException(401, "Please sign in.")
        return t

    def set_cookie(request: Request, response: Response, value: str, max_age: int) -> None:
        response.set_cookie(COOKIE, value, max_age=max_age, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", path=(request.scope.get("root_path") or "") + "/")

    @app.get("/api/account")
    def me(request: Request):
        if not accounts.on:
            return {"available": False, "signed_in": False}
        profile = accounts.current(request)
        return {"available": True, "signed_in": bool(profile), "customer": profile, "settings": accounts.settings()}

    @app.post("/api/account/signup", status_code=202)
    def signup(b: SignupIn, request: Request):
        limit(request, b.email)
        return _pass(accounts.call("POST", "/signup", json=b.model_dump()))

    @app.post("/api/account/code", status_code=202)
    def send_code(b: EmailIn, request: Request):
        limit(request, b.email)
        return _pass(accounts.call("POST", "/codes", json=b.model_dump()))

    @app.post("/api/account/verify")
    def verify(b: VerifyIn, request: Request):
        limit(request)
        r = accounts.call("POST", "/sessions", json=b.model_dump())
        if r.status_code != 201:
            d = _detail(r)
            raise HTTPException(r.status_code if r.status_code < 500 else 502, d.get("message") if isinstance(d, dict) else d)
        s = r.json()
        out = JSONResponse({"signed_in": True, "customer": s["customer"]})
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

    @app.put("/api/account")
    def update(b: ProfileIn, request: Request):
        return _pass(accounts.call("PUT", "/me", token(request), json=b.model_dump()))

    @app.delete("/api/account")
    def delete(request: Request):
        r = accounts.call("DELETE", "/me", token(request))
        if r.status_code != 204:
            _pass(r)
        out = JSONResponse({"deleted": True})
        set_cookie(request, out, "", 0)
        return out

    @app.put("/api/account/photo")
    async def put_photo(request: Request):
        data = await request.body()
        if len(data) > MAX_PHOTO:
            raise HTTPException(413, "That picture is too large.")
        return _pass(accounts.call("PUT", "/me/photo", token(request), content=data,
                                   headers={"Content-Type": request.headers.get("content-type", "application/octet-stream")}))

    @app.get("/api/account/photo")
    def get_photo(request: Request):
        r = accounts.call("GET", "/me/photo", token(request))
        if r.status_code != 200:
            raise HTTPException(404, "No photo")
        return Response(r.content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.delete("/api/account/photo")
    def delete_photo(request: Request):
        return _pass(accounts.call("DELETE", "/me/photo", token(request)))

    @app.get("/api/account/orders")
    def orders(request: Request):
        """The signed-in customer's orders on any device, newest first, with their items for Buy again."""
        mine = _pass(accounts.call("GET", "/me/orders", token(request)))
        out = []
        for o in mine[:20]:
            sale = sale_lines(o["sale_id"])
            if sale:
                out.append({**sale, "order_id": o["sale_id"], "points_earned": o["earned_points"],
                            "points_spent": o["redeemed_points"], "at": o["created_at"]})
        return out
