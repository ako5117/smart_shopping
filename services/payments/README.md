# Payments Service — M-Pesa (Daraja STK Push)

Starts an M-Pesa payment prompt on the customer's phone, receives the result from Safaricom, and records it. Card payments will be added in Phase 2.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/payments/mpesa/stk-push` | Start a payment: `{"sale_id": "SALE-0001", "phone": "0708374149", "amount": 100}` → `202` with `payment_id` |
| `POST` | `/payments/mpesa/callback/{secret}` | Daraja posts the result here. Not called by our apps. |
| `GET` | `/payments/{payment_id}` | Current status: `pending`, `paid`, `failed`, `cancelled` |
| `POST` | `/payments/{payment_id}/refresh` | Asks Daraja for the result directly if the callback has not arrived |
| `GET` | `/health` | Health check |

Amounts are whole shillings; Daraja does not accept decimals. Phone numbers are accepted as `07…`, `01…`, `+254…` or `254…`.

## Setup

1. Create an account at [developer.safaricom.co.ke](https://developer.safaricom.co.ke), create an app with **M-Pesa Express (Sandbox)** enabled, and copy the Consumer Key and Consumer Secret.
2. Copy `.env.example` to `.env` and fill it in. The sandbox shortcode is `174379`; the sandbox passkey is shown on the Daraja portal's test credentials page.
3. Daraja can only deliver callbacks to a public HTTPS URL. For local development, run a tunnel (e.g. `ngrok http 8000`) and put its URL in `PUBLIC_BASE_URL`. Set `CALLBACK_SECRET` to a long random string.

```bash
cd services/payments
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
set -a && source .env && set +a                     # or export the variables another way
uvicorn app.main:create_app --factory --reload --port 8000
```

Try it:

```bash
curl -X POST localhost:8000/payments/mpesa/stk-push \
  -H "Content-Type: application/json" \
  -d '{"sale_id": "TEST-001", "phone": "0708374149", "amount": 1}'
```

## Tests

```bash
pytest
```

Tests use a mocked Daraja; no credentials or network needed.

## How it handles the awkward cases

- **Duplicate callbacks:** a payment already `paid`, `failed` or `cancelled` is never changed again, and the paid notification fires once.
- **Callback never arrives:** call `/refresh`, which uses Daraja's STK query. While the customer hasn't responded, the payment stays `pending`.
- **Forged callbacks:** Daraja does not sign callbacks, so the callback URL includes a secret path segment; requests with the wrong secret get `404`.
- **Customer cancels:** result code `1032` is recorded as `cancelled`, separate from failures.
- **Token expiry:** the access token is cached and refreshed 5 minutes before Daraja's one-hour expiry.

## Going live

Production needs a real Paybill or Till linked to the app through **Go Live** on the Daraja portal, which issues a production passkey. The shortcode belongs to whoever receives the money — normally the store, not Awesomtech. For a Till, set `DARAJA_TRANSACTION_TYPE=CustomerBuyGoodsOnline` and `DARAJA_PARTY_B` to the till number.

## Next

- Notify the Inventory Service when a payment is paid, so the sale is committed to the stock ledger (the `on_paid` hook in `app/main.py`).
- Move storage from SQLite to the shared database once it is set up.
- Card payments (Phase 2).
