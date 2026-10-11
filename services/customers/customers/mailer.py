"""Sending email: a stand-in for now.

EMAIL_PROVIDER=outbox (the default) keeps each message in the "emails" table instead of sending it, and the
dashboard shows them (Loyalty page). That's enough for a demo: the sign-up code is read from there.

A store connects its own email service by adding a sender here with the same send(to, subject, body)
method (SMTP, or their provider's API) and choosing it with EMAIL_PROVIDER. Until then nothing leaves the
server, so a real customer would never receive their code: preflight warns about it on a live store.
"""

from datetime import datetime, timezone


class OutboxMailer:
    """Keeps emails in the database (shown on the dashboard) instead of sending them."""

    name = "outbox"

    def __init__(self, db):
        self.db = db

    def send(self, to: str, subject: str, body: str) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO emails (to_email, subject, body, provider, created_at) VALUES (?, ?, ?, ?, ?)",
                      (to, subject, body, self.name, datetime.now(timezone.utc).isoformat()))


def mailer_from_env(db, provider: str):
    provider = (provider or "outbox").strip().lower()
    if provider == "outbox":
        return OutboxMailer(db)
    raise ValueError(f"Unknown EMAIL_PROVIDER {provider!r}: only 'outbox' (the stand-in) is built so far")
