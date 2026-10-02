"""Sending one SMS. Africa's Talking for real texts; the simulator for demos and development.

Africa's Talking: POST {base}/version1/messaging, form fields username, to, message and (optionally) from
(the store's registered sender ID), header apiKey. The username "sandbox" uses the sandbox, whose texts
appear in the simulator on the Africa's Talking website instead of on phones.
"""

import secrets
from dataclasses import dataclass
from typing import Optional

import httpx

LIVE_URL = "https://api.africastalking.com"
SANDBOX_URL = "https://api.sandbox.africastalking.com"

# Recipient status codes that mean the number itself won't take the text: don't retry.
PERMANENT = {403: "Invalid phone number", 404: "Unsupported number type", 406: "The customer has blocked texts",
             402: "Sender ID not approved", 409: "The customer has opted out of promotional texts"}


@dataclass(frozen=True)
class SendResult:
    outcome: str  # sent | retry | failed
    ref: Optional[str] = None
    cost: Optional[str] = None
    error: str = ""


class SimulatorSms:
    """Records texts as sent without sending anything. For demos: the texts show on the dashboard."""

    name = "simulator"

    def send(self, to: str, body: str) -> SendResult:
        return SendResult("sent", ref="SIM-" + secrets.token_hex(5), cost="KES 0 (simulated)")


class AfricasTalkingSms:
    name = "africastalking"

    def __init__(self, username: str, api_key: str, sender_id: str = "", http: Optional[httpx.Client] = None):
        if not username or not api_key:
            raise ValueError("Africa's Talking needs a username and an API key")
        self.username, self.api_key, self.sender_id = username, api_key, sender_id
        base = SANDBOX_URL if username == "sandbox" else LIVE_URL
        self.http = http or httpx.Client(base_url=base, timeout=20.0)

    def send(self, to: str, body: str) -> SendResult:
        form = {"username": self.username, "to": to, "message": body}
        if self.sender_id:
            form["from"] = self.sender_id
        try:
            r = self.http.post("/version1/messaging", data=form,
                               headers={"apiKey": self.api_key, "Accept": "application/json"})
        except httpx.HTTPError as e:
            return SendResult("retry", error=f"Africa's Talking unreachable: {e}")
        if r.status_code in (401, 403) and "Recipients" not in r.text:
            return SendResult("failed", error=f"Africa's Talking refused the API key or username ({r.status_code})")
        try:
            data = r.json()["SMSMessageData"]
            recipient = data["Recipients"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            return SendResult("retry", error=f"Unexpected answer from Africa's Talking ({r.status_code}): {r.text[:200]}")
        code = int(recipient.get("statusCode", 0))
        if code in (100, 101, 102):  # processed, sent, queued
            return SendResult("sent", ref=recipient.get("messageId"), cost=recipient.get("cost"))
        if code in PERMANENT:
            return SendResult("failed", error=f"{PERMANENT[code]} ({recipient.get('status', code)})")
        return SendResult("retry", error=f"{recipient.get('status', 'Not sent')} ({code})")
