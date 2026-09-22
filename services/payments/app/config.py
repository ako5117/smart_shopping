import os
from dataclasses import dataclass

BASE_URLS = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
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
    db_path: str

    @property
    def daraja_base_url(self) -> str:
        return BASE_URLS[self.daraja_env]

    @property
    def callback_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/payments/mpesa/callback/{self.callback_secret}"


def load_settings() -> Settings:
    env = os.getenv("DARAJA_ENV", "sandbox")
    if env not in BASE_URLS:
        raise ValueError(f"DARAJA_ENV must be one of {list(BASE_URLS)}, got {env!r}")
    shortcode = os.getenv("DARAJA_SHORTCODE", "174379")
    return Settings(
        daraja_env=env,
        consumer_key=os.getenv("DARAJA_CONSUMER_KEY", ""),
        consumer_secret=os.getenv("DARAJA_CONSUMER_SECRET", ""),
        shortcode=shortcode,
        passkey=os.getenv("DARAJA_PASSKEY", ""),
        transaction_type=os.getenv("DARAJA_TRANSACTION_TYPE", "CustomerPayBillOnline"),
        party_b=os.getenv("DARAJA_PARTY_B") or shortcode,
        public_base_url=os.getenv("PUBLIC_BASE_URL", ""),
        callback_secret=os.getenv("CALLBACK_SECRET", ""),
        db_path=os.getenv("PAYMENTS_DB_PATH", "payments.db"),
    )
