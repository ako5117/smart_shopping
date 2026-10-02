"""HTTP API for the Notification Service.

    uvicorn notify.api:create_app --factory --port 8070

A background thread checks the Inventory and Dispatch Services every NOTIFY_POLL_S seconds (default 5)
and sends the texts that are due. Only the store dashboard calls the API, to list texts and resend one.

Settings (environment):
    SMS_PROVIDER     africastalking | simulator | empty (texts are off; nothing is watched or sent)
    AT_USERNAME      Africa's Talking username ("sandbox" for the sandbox)
    AT_API_KEY       Africa's Talking API key
    AT_SENDER_ID     the store's approved sender ID (optional; without it texts come from a shared number)
    STORE_ID, STORE_NAME, INVENTORY_URL, DISPATCH_URL
"""

import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query

from .db import Database, database_from_env
from .service import Notifier, Settings
from .sms import AfricasTalkingSms, SimulatorSms

log = logging.getLogger("notify")


def sender_from_env():
    """The SMS provider from SMS_PROVIDER, or None when texts are off."""
    provider = os.getenv("SMS_PROVIDER", "").strip().lower()
    if provider == "simulator":
        return SimulatorSms()
    if provider == "africastalking":
        return AfricasTalkingSms(os.getenv("AT_USERNAME", ""), os.getenv("AT_API_KEY", ""),
                                 os.getenv("AT_SENDER_ID", ""))
    if provider:
        raise ValueError(f"Unknown SMS_PROVIDER {provider!r}: use africastalking, simulator or leave it empty")
    return None


def create_app(db: Optional[Database] = None, sender=None, inventory: Optional[httpx.Client] = None,
               dispatch: Optional[httpx.Client] = None, start_worker: bool = True) -> FastAPI:
    db = db or database_from_env()
    sender = sender if sender is not None else sender_from_env()
    settings = Settings(os.getenv("STORE_ID", "001"), os.getenv("STORE_NAME", "Smart Shopping"))
    inventory = inventory or httpx.Client(base_url=os.getenv("INVENTORY_URL", "http://localhost:8010"), timeout=5.0)
    if dispatch is None and os.getenv("DISPATCH_URL"):
        dispatch = httpx.Client(base_url=os.environ["DISPATCH_URL"], timeout=5.0)
    notifier = Notifier(db, sender, settings, inventory, dispatch) if sender else None
    poll = float(os.getenv("NOTIFY_POLL_S", "5"))

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        worker = None
        if notifier and start_worker:
            def run():
                while not stop.wait(poll):
                    try:
                        notifier.tick()
                    except Exception:  # keep going whatever happens; the next round tries again
                        log.exception("Notification round failed")
            worker = threading.Thread(target=run, name="notify-worker", daemon=True)
            worker.start()
            log.info("Texting customers through %s", sender.name)
        else:
            log.info("Texts are off (SMS_PROVIDER is empty)")
        yield
        stop.set()
        if worker:
            worker.join(timeout=poll + 5)

    app = FastAPI(title="Smart Shopping - Notification Service", lifespan=lifespan)
    app.state.notifier = notifier

    def need():
        if notifier is None:
            raise HTTPException(503, "Texts are off: set SMS_PROVIDER (see deploy/README.md)")
        return notifier

    @app.get("/health")
    def health():
        return {"status": "ok", "provider": sender.name if sender else None}

    @app.get("/messages")
    def messages(limit: int = Query(50, ge=1, le=500), sale_id: Optional[str] = None):
        if notifier is None:
            return []
        return notifier.messages(limit, sale_id)

    @app.post("/messages/{message_id}/resend")
    def resend(message_id: int):
        n = need()
        m = n.resend(message_id)
        if m is None:
            raise HTTPException(404, f"No text {message_id}")
        if m["status"] != "queued":
            raise HTTPException(409, f"Can't resend to {m['to_number']}: not a Kenyan mobile number")
        return m

    return app
