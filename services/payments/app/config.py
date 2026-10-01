import os
from dataclasses import dataclass

BASE_URLS = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
    "simulator": "http://localhost:8099",  # tools/mpesa_simulator; override with DARAJA_SIM_URL
}


@dataclass(frozen=True)
class Settings:
    daraja_env: str
    consumer_key: str
    consumer_secret: str
    shortcode: str
    passkey: str
    transaction_type: str
    party_b: str
    public_base_url: str
    callback_secret: str
    db_path: str  # SQLite file, or a postgresql:// URL for the shared database
    inventory_url: str = ""
    db_schema: str = "payments"

    @property
    def daraja_base_url(self) -> str:
        if self.daraja_env == "simulator":
            return os.getenv("DARAJA_SIM_URL", BASE_URLS["simulator"])
        return BASE_URLS[self.daraja_env]

    @property
    def callback_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/payments/mpesa/callback/{self.callback_secret}"


def load_settings() -> Settings:
    env = os.getenv("DARAJA_ENV", "sandbox")
    if env not in BASE_URLS:
        raise ValueError(f"DARAJA_ENV must be one of {list(BASE_URLS)}, got {env!r}")
    shortcode = os.getenv("DARAJA_SHORTCODE", "174379")
    public_base_url = os.getenv("PUBLIC_BASE_URL", "")
    callback_secret = os.getenv("CALLBACK_SECRET", "")
    if not callback_secret:
        raise ValueError("CALLBACK_SECRET is not set. Set it to a long random string; it is part of the "
                         "M-Pesa callback URL so only Daraja can post payment results.")
    if env in ("sandbox", "production") and not public_base_url.startswith("https://"):
        raise ValueError(f"PUBLIC_BASE_URL must be this server's public https:// address (e.g. "
                         f"https://shop.example.co.ke) when DARAJA_ENV is {env}; Daraja only sends callbacks "
                         f"over HTTPS. Got {public_base_url!r}. For a demo without Safaricom, use "
                         f"DARAJA_ENV=simulator.")
    return Settings(
        daraja_env=env,
        consumer_key=os.getenv("DARAJA_CONSUMER_KEY", ""),
        consumer_secret=os.getenv("DARAJA_CONSUMER_SECRET", ""),
        shortcode=shortcode,
        passkey=os.getenv("DARAJA_PASSKEY", ""),
        transaction_type=os.getenv("DARAJA_TRANSACTION_TYPE", "CustomerPayBillOnline"),
        party_b=os.getenv("DARAJA_PARTY_B") or shortcode,
        public_base_url=public_base_url,
        callback_secret=callback_secret,
        db_path=os.getenv("DATABASE_URL") or os.getenv("PAYMENTS_DB_PATH", "payments.db"),
        db_schema=os.getenv("DATABASE_SCHEMA", "payments"),
        inventory_url=os.getenv("INVENTORY_URL", ""),
    )
