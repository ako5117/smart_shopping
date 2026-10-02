# Card simulator

Imitates the parts of Paystack the Payments Service uses for card payments, so card checkout can be demoed without a Paystack account or real money. It is **not** Paystack and never moves money. Its payment page says so, and asks people not to type a real card number.

It imitates:
- `POST /transaction/initialize`
- `GET /transaction/verify/{reference}`
- the hosted payment page and the redirect back
- the signed `charge.success` / `charge.failed` webhook

```bash
cd tools/card_simulator
pip install -r requirements.txt
uvicorn sim:app --port 8098
```

Run the Payments Service with `CARD_PROVIDER=simulator` (and `CARD_SIM_URL` if not on `http://localhost:8098`).

| Card number | Result |
|---|---|
| `4084 0840 8408 4081` | Paid (Visa •••• 4081) |
| `4000 0000 0000 0002` | Declined |
| anything else | Declined ("Invalid card number") |
| **Cancel and go back** | Not completed; the shop offers to try again |

These are Paystack's own test numbers, so a demo works the same after switching to a Paystack test key.

| Setting | Default | Meaning |
|---|---|---|
| `CARD_SIM_SECRET` | `sk_test_simulator` | The "secret key": API calls must send it, and webhooks are signed with it |
| `CARD_SIM_PUBLIC_URL` | `/card-sim` | Where customers' browsers reach the payment page (through the proxy) |
| `CARD_SIM_WEBHOOK_URL` | (none) | Where to post the signed webhook, e.g. `http://proxy/pay/payments/card/webhook` |

In `docker-compose.yml` it runs with the `demo` profile.
