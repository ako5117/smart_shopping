# Payments Service: M-Pesa and cards

Starts an M-Pesa payment prompt on the customer's phone (Daraja STK Push), or a card payment on the card provider's hosted page (Paystack). It receives each result and records it. When a payment succeeds, it commits the sale in the Inventory Service.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/payments/mpesa/stk-push` | Start a payment: `{"sale_id": "SALE-0001", "phone": "0708374149", "amount": 100}` → `202` with `payment_id` |
| `POST` | `/payments/mpesa/callback/{secret}` | Daraja posts the result here. Not called by our apps. |
| `GET` | `/payments/{payment_id}` | Current status: `pending`, `paid`, `failed`, `cancelled` |
| `POST` | `/payments/{payment_id}/refresh` | Asks Daraja (or the card provider) for the result directly if the callback has not arrived |
| `GET` | `/payments/methods` | Which ways to pay are on: `{"mpesa": true, "card": true, "card_provider": "paystack"}` |
| `POST` | `/payments/card/checkout` | Start a card payment: `{"sale_id", "amount", "email", "return_url"}` → `201` with `payment_id` and `checkout_url` |
| `POST` | `/payments/card/webhook` | The card provider posts results here, signed. Not called by our apps. |
| `GET` | `/health` | Health check |

Amounts are whole shillings; Daraja does not accept decimals. Phone numbers are accepted as `07…`, `01…`, `+254…` or `254…`.

## Setup

1. Create an account at [developer.safaricom.co.ke](https://developer.safaricom.co.ke), create an app with **M-Pesa Express (Sandbox)** enabled, and copy the Consumer Key and Consumer Secret.
2. Copy `.env.example` to `.env` and fill it in. The sandbox shortcode is `174379`; the sandbox passkey is shown on the Daraja portal's test credentials page.
3. Daraja can only deliver callbacks to a public HTTPS URL. The service won't start on `sandbox` or `production` without an `https://` `PUBLIC_BASE_URL` and a `CALLBACK_SECRET`. For local development, run a tunnel (e.g. `ngrok http 8000`) and put its URL in `PUBLIC_BASE_URL`. Set `CALLBACK_SECRET` to a long random string.

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

## Demo without Safaricom

Set `DARAJA_ENV=simulator` and run `tools/mpesa_simulator` (or use the `demo` profile in the repo's `docker-compose.yml`). See [`tools/mpesa_simulator/README.md`](../../tools/mpesa_simulator/README.md).

## Tests

```bash
pytest
```

Tests use a mocked Daraja; no credentials or network needed. They run on SQLite in memory; set `TEST_DATABASE_URL=postgresql://...` to run them against PostgreSQL too.

## Storage

`DATABASE_URL` points at the shared PostgreSQL database (`postgresql://user:password@host:5432/dbname`), with this service's tables in the `DATABASE_SCHEMA` schema (default `payments`). Without it, payments are kept in a SQLite file, `PAYMENTS_DB_PATH` (default `payments.db`), which is fine for development. `app/sqldb.py` is shared with the Inventory Service and must stay identical to `services/inventory/inventory/sqldb.py`.

## How it handles the awkward cases

- **Duplicate callbacks:** a payment already `paid`, `failed` or `cancelled` is never changed again, and the paid notification fires once.
- **Callback never arrives:** call `/refresh`, which uses Daraja's STK query. While the customer hasn't responded, the payment stays `pending`.
- **Forged callbacks:** Daraja does not sign callbacks, so the callback URL includes a secret path segment; requests with the wrong secret get `404`.
- **Customer cancels:** result code `1032` is recorded as `cancelled`, separate from failures.
- **Token expiry:** the access token is cached and refreshed 5 minutes before Daraja's one-hour expiry.

## Going live

Production needs a real Paybill or Till linked to the app through **Go Live** on the Daraja portal, which issues a production passkey. The shortcode belongs to whoever receives the money — normally the store, not Awesomtech. For a Till, set `DARAJA_TRANSACTION_TYPE=CustomerBuyGoodsOnline` and `DARAJA_PARTY_B` to the till number.

## Inventory

When `INVENTORY_URL` is set, a confirmed payment calls `POST /sales/{sale_id}/commit` on the Inventory Service, which takes the items out of stock. The checkout app creates the sale in the Inventory Service (with its items) before starting the payment. The commit is retried three times; if the Inventory Service stays unreachable, the payment still stands and an error is logged so the sale can be committed again.

## Cards

Card details are typed on the card provider's own page, never on ours (PCI DSS SAQ A: card numbers never reach Smart Shopping).

1. **Start.** The app calls `POST /payments/card/checkout`. The service asks the provider for a checkout page for that amount in KES, cards only, and the customer's browser goes there.
2. **Pay.** The customer pays, with 3-D Secure if their bank asks for it. The provider sends them back to `return_url`, the shop or Scan & Go.
3. **Confirm.** The provider also posts a webhook, signed with HMAC-SHA512 of the body and the secret key (`x-paystack-signature`). An unsigned or wrongly signed webhook gets `401`.
4. **Check before counting.** The service doesn't take the webhook's word for it. Either way (webhook, or the app's `refresh` when the customer comes back), it asks the provider for the transaction. The payment counts only if that says success **and** the amount and currency match what was asked. A mismatch is marked failed and logged loudly.

The sale's payment reference becomes `CARD-<provider transaction id>`. The receipt shows the card as `Visa •••• 4081`.

A customer who leaves the card page without paying isn't marked as cancelled: Paystack reports that as "abandoned", and they could still finish. The apps offer to try again, by card or M-Pesa, after a few seconds.

### Setting up Paystack

1. Open a Paystack business account for Kenya (KES). On the dashboard, under **Settings → API Keys & Webhooks**, copy the secret key: `sk_test_...` for testing, `sk_live_...` for real money.
2. Set the webhook URL there to `https://<your domain>/pay/payments/card/webhook`. The proxy lets it through without a login; the signature protects it.
3. In `.env`: `CARD_PROVIDER=paystack` and `PAYSTACK_SECRET_KEY=sk_...`. The service won't start with `CARD_PROVIDER=paystack` and no valid key.
4. Paystack's test cards work with a test key, e.g. `4084 0840 8408 4081` (any future expiry, CVV `408`).

`CARD_PROVIDER=simulator` uses `tools/card_simulator` instead, for demos (see its README). Another provider (Pesapal, Flutterwave) would be another class in `app/cards.py` with the same four methods.

The Paystack calls follow its published API (`/transaction/initialize`, `/transaction/verify/{reference}`, signed webhooks). Before going live, run one payment with a Paystack **test** key end to end.

## Next

- Refunds (by hand on the provider's dashboard for now).
