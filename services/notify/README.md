# Notification Service

Texts online customers when their order moves on. Staff see every text, and can send one again, on the dashboard's **Online orders** page.

| When | Text (one SMS, at most 160 characters) |
|---|---|
| The order is paid, collect | `Mama Mboga Mart: order 0000AAAA is paid, KES 200. We'll text you when it's ready to collect.` |
| The order is paid, delivery | `... is paid, KES 350. Your delivery code is 0427. Give it to the rider when your order arrives.` |
| A collect order is packed | `Mama Mboga Mart: order 0000AAAA is ready. Collect it at the counter and show the pass on your order page.` |
| A delivery leaves with its rider | `Mama Mboga Mart: order 0000AAAA is on the way with otieno (0722000111). Delivery code 0427.` |

Texts use only GSM characters (no curly quotes or emoji), so each one is a single SMS. Store and rider names are shortened if they'd make it longer.

## How it works

- **It watches; nobody calls it.** Every few seconds it reads the paid online orders from the Inventory Service and the deliveries out with riders from the Dispatch Service. Neither service knows it exists, so if it's down nothing else is affected, and when it's back it catches up.
- **Each update is texted once.** Texts go into an outbox (`messages`) keyed by update and order (`paid:WEB-...`), whatever restarts or repeats happen.
- **Only recent updates.** An update more than 2 hours old isn't texted, so starting the service for the first time doesn't text every past customer.
- **Retries.** If the SMS provider can't be reached or is busy, the text is tried again after 1, 5 and 15 minutes, then marked failed. A number that can't take texts (not a Kenyan mobile, blocked, opted out) fails at once.
- **The delivery code** comes from the Dispatch Service (`?include_pin=true`), the same way the online shop gets it. The dashboard shows it as `****`, so it stays the customer's alone.

## Settings

| Variable | |
|---|---|
| `SMS_PROVIDER` | `africastalking`, `simulator` (texts are recorded, and shown on the dashboard, but not sent) or empty (off) |
| `AT_USERNAME` | Africa's Talking app username; `sandbox` uses their sandbox, whose texts show in the simulator on their website |
| `AT_API_KEY` | Africa's Talking API key |
| `AT_SENDER_ID` | The store's approved sender ID (e.g. `MAMAMBOGA`). Empty: texts come from Africa's Talking's shared number |
| `STORE_ID`, `STORE_NAME` | Which store's orders to watch, and the name at the start of each text |
| `INVENTORY_URL`, `DISPATCH_URL` | The other services. Without `DISPATCH_URL`, delivery orders aren't texted |
| `NOTIFY_POLL_S` | How often to check, in seconds (default 5) |
| `DATABASE_URL` / `NOTIFY_DB_PATH` | PostgreSQL (schema `notify`) or a SQLite file |

## Setting up Africa's Talking

1. Create an account at africastalking.com. Start in the **sandbox**: username `sandbox`, and an API key from the sandbox app's settings. Texts appear in their online simulator, not on phones.
2. For real texts, create a live app, top up its SMS balance, and generate an API key for it. Put the app's username and key in `./scripts/configure.sh`.
3. **Sender ID** (optional): request one in the Africa's Talking dashboard so texts come from the store's name. Approval by the networks takes some days; until then leave it empty.

Each text is one SMS. Check the current per-message price on Africa's Talking for Safaricom, Airtel and Telkom numbers; the cost of each text is shown on the dashboard.

## API

Internal only (the dashboard calls it; the proxy doesn't publish it).

| | |
|---|---|
| `GET /health` | `{"status": "ok", "provider": "africastalking"}` |
| `GET /messages?limit=50&sale_id=` | Latest texts, newest first |
| `POST /messages/{id}/resend` | Send a text again. 409 if the number can't take texts |

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/db pytest -q   # against PostgreSQL
```
