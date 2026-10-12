# Online shop

Customers order online, pay with M-Pesa, and collect from the store or have a rider deliver. This is Phase 2: the shop reads live shelf data to show what's really available, and suggests substitutes when something runs low or sells out.

1. **Browse.** Products with prices, grouped by category, with search. Each product shows **In stock**, **Only N left** or **Out of stock**, refreshed every 15 seconds.
2. **Substitutes.** A sold-out product shows up to three in-stock products from the same category, closest in price first, each with an **Add** button. They also appear when the customer already has all that's left of a product in their basket.
3. **Pay.** The customer gives their name and phone number, and pays with **M-Pesa** or by **card** (when the Payments Service has cards on). The shop records the order in the Inventory Service, which holds the items. For M-Pesa, the Payments Service then sends the prompt to the phone. For a card, the customer goes to the card provider's payment page and comes back to the shop, which confirms the payment. If it fails, or they leave without paying, they can try again either way.
4. **Packing.** Paid orders appear on the dashboard's **Online orders** page. It shows the shelf zone for each item, or "Store room" for items not on a sensor shelf. Staff press **Packed: ready for collection**, and the customer's page changes to **Ready to collect**.
5. **Collect.** The customer shows the pass (code and QR) at the counter. Staff check it on the dashboard's **Exit check** page, which shows it as an online order for that customer's name. Once confirmed, the pass turns grey and reads **Collected**.

## Delivery

When the Dispatch Service is set (`DISPATCH_URL`), checkout also offers **Deliver to me**.

- **Before shopping:** the top of the shop asks **Delivery or collect?** The customer shares their location or taps it on a map (Leaflet with OpenStreetMap tiles, loaded only when they open it). The Dispatch Service answers with the distance and the fees, or says the place is too far (beyond 20 km by default) and that the order would need to be collected from the store. The phone remembers the place.
- **At checkout:** **Express** (out as soon as it's packed, about an hour) or **Choose a time** (a 2-hour slot, today or the next two days), each with its fee, then the address and directions for the rider. The fee is quoted again by the Dispatch Service when the order is placed, never taken from the browser. It's added to the order in the Inventory Service and is part of what M-Pesa charges.
- **Order page:** shows the customer's **4-digit delivery code** as soon as the order is paid. It goes **Paid → Packing → On the way → Delivered**. Once a rider has the job, it shows their name with a **Call** button.
- **At the door:** the customer gives the rider the code, and the order page changes to **Delivered**. Without the code the rider can't mark it delivered.
- **Problems:** if the rider reports a problem, the page says the store will call the customer.
- **Delivery unavailable:** if the Dispatch Service is down, **Deliver to me** isn't offered. If it fails at the moment of ordering, the order is cancelled before any payment request and its items are released.

## Returns

A paid order's page has a **Returns** card while the return period is open (14 days by default, `RETURN_DAYS` in the Inventory Service).

- **Asking:** **Return items** opens a list of the order's items. Perishables are shown but can't be chosen. The customer picks how many of each, gives a reason and confirms the items are unopened and in the same state.
- **At the store:** they bring the items with the order page. Staff check them, a manager approves, and the store refunds them.
- **Status:** the card shows each return as it moves on: bring the items in, approved with the refund on its way, refunded (and how), or refused with the reason.
- **Who can ask:** only someone with the order page's address (order and payment id) can see or ask for its returns.

## Report a problem

When the Customers Service is set, customers can tell the store about an order, the store, the app, or an idea.

- **Where:** from the Account tab, or **Problem with this order?** on an order page, which fills in the order number.
- **What they send:** a short message, an optional photo, and optionally a phone or email.
- **App problems:** "The app isn't working" also sends the page, screen size, browser and the page's last errors.
- **Replies:** staff answer on the dashboard's Reports page. Signed-in customers see the reply on their Account tab; others see it from the reference the phone remembers.
- **Scan & Go:** has the same form at the bottom of its page.

See [`services/customers`](../../services/customers).

## Accounts and loyalty points

When the Customers Service is set (`CUSTOMERS_URL`), the shop has an **Account** tab. See [`services/customers`](../../services/customers) for the rules.

- **Sign up:** name, phone, email and date of birth, plus gender and a photo if the customer wants. They accept the privacy notice and confirm their email with a 6-digit code. Signing in later works the same way: email, then the code. There's no password.
- **Signed in:**
  - Checkout fills in their name and number.
  - Paid orders earn points (1 per KES 100 of goods by default).
  - With enough points, **Use points** takes whole blocks off the order (100 points = KES 50). The discount is worked out by the Customers Service and recorded on the sale, never taken from the browser.
  - If the order is cancelled, expires or fails before payment, the points come back.
- **Orders tab:** shows the account's orders from any device, with **Buy again**. Signed out, it shows orders on this phone, as before.
- **Customers Service down:** shopping, checkout and collection all still work. Points can't be spent until it's back, and the order says so.

## Language

English or Kiswahili: the button at the top right switches. The phone remembers the choice, and so does a signed-in customer's account. Orders record the language, so their texts come in it too. See [`docs/languages.md`](../../docs/languages.md).

Riders, assignment and the handover are covered in [`services/dispatch`](../../services/dispatch) and [`apps/rider`](../rider).

## What "available" means

For each product, the shop promises only:

| | Source |
|---|---|
| committed stock | Inventory Service ledger |
| − items held by unpaid orders (online or Scan & Go), for 15 minutes | Inventory Service |
| − items shoppers in the store picked up in the last 5 minutes and haven't put back | Shelf Service events |
| − a buffer of 1, in case the shelf count is off | `IN_STORE_BUFFER` |

The shelf part is what makes it live. Someone holding the last two loaves in the store isn't competing with an online customer for them. Shelf events still never change committed stock; they only make the online promise more careful. An item picked up and paid for with Scan & Go is counted twice for those few minutes (once as picked up, once as sold), so the shop may briefly show less than is really there, never more.

Without shelf sensors (`SHELF_EVENTS_PATH` unset), the shop works from the ledger alone. The page then says "Stock updated" instead of "Live from the store shelves".

## No overselling

- The shop checks availability before it records an order.
- The Inventory Service checks again (`check_stock`) in the same transaction that records the order. If two customers go for the last one, the second is refused.
- On a refusal, the checkout page shows what changed, offers **Change to N** or substitutes, and nothing is charged.
- An unpaid order holds its items for 15 minutes. After that they're available again, and a new payment request for that order is refused; the customer is asked to place it again.
- If the M-Pesa request can't be sent, the order is cancelled at once and its items are released.

## How it stays safe

As in Scan & Go:

- Prices and totals come from the Inventory Service, not the browser.
- Order IDs are random (`WEB-` plus 16 hex characters), and looking one up needs its payment ID too. Only online orders can be seen through the shop.
- M-Pesa prompts are limited to 3 per phone number per 10 minutes.
- Phone numbers are masked on the receipt.
- Orders take up to 20 of one product and 30 different products.

## Run

```bash
cd apps/storefront
pip install -r requirements-dev.txt
INVENTORY_URL=http://localhost:8010 PAYMENTS_URL=http://localhost:8000 \
SHELF_EVENTS_PATH=../../services/shelf/shelf_events.jsonl \
uvicorn storefront.app:create_app --factory --port 8040
```

In `docker-compose.yml` it runs at `/store/`, public, and reads the shelf events read-only.

| Setting | Default | Meaning |
|---|---|---|
| `STORE_ID` | `001` | Store the orders are for |
| `STORE_NAME` | `Smart Shopping` | Shown in the header, on the pass and as the collection point |
| `INVENTORY_URL` | `http://localhost:8010` | Inventory Service |
| `PAYMENTS_URL` | `http://localhost:8000` | Payments Service (its `INVENTORY_URL` must be set so paid orders leave stock) |
| `DISPATCH_URL` | (none) | Dispatch Service; without it the shop is collect-only |
| `SHELF_EVENTS_PATH` | (none) | The Shelf Service's event log (JSON lines) |
| `PICKED_WINDOW_MIN` | `5` | How long a picked-up item counts as in someone's basket |
| `IN_STORE_BUFFER` | `1` | Kept back from online sale, per product |
| `FEW_LEFT` | `5` | At or below this, the shop shows "Only N left" |
| `STK_LIMIT_PER_PHONE` | `3` | M-Pesa prompts per phone number per 10 minutes |

## Endpoints

Requests with `X-Language: sw` get their messages in Kiswahili (delivery times, offers and reasons too).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The shop |
| `GET` | `/api/store` | Store name, how long orders are held, and how delivery is priced (store location, distance limit) |
| `GET` / `PUT` / `DELETE` | `/api/account` | The signed-in customer (or `signed_in: false`); change details; delete the account |
| `POST` | `/api/account/signup`, `/api/account/code`, `/api/account/verify`, `/api/account/signout` | Create an account, send a sign-in code, check a code (sets the session cookie), sign out |
| `PUT` / `GET` / `DELETE` | `/api/account/photo` | Profile photo |
| `PUT` | `/api/account/language` | `{"language": "en" or "sw"}`: keep the shop's language on the account |
| `GET` | `/api/account/orders` | The account's orders with their items |
| `GET` / `POST` | `/api/orders/{order_id}/payments/{payment_id}/returns` | What can be returned and the order's returns; ask for a return: `{lines: [{product_id, qty}], reason, unopened: true}` |
| `POST` | `/api/reports` | Report a problem: `{kind, message, order_id?, contact?, context?, photo_b64?}` |
| `GET` | `/api/reports/{report_id}`, `/api/account/reports` | A report's status and reply; the signed-in customer's reports |
| `GET` | `/api/delivery/quote?lat=&lng=` | Can we deliver here? Distance, express and scheduled fees and open slots, or `deliverable: false` with the reason |
| `GET` | `/api/catalogue` | Products with price, category, status (`in` / `few` / `out`), how many can be ordered, and substitutes |
| `POST` | `/api/orders` | `{"name", "phone", "items": [{"product_id", "qty"}]}`, plus `"payment_method": "card"` and `"email"` to pay by card (the answer then has a `checkout_url`), plus `"fulfilment": "delivery"` and `"delivery": {"address", "notes", "lat", "lng", "tier": "express" or "scheduled", "slot_start"}` for a delivery, plus `"use_points": true` for a signed-in customer to spend points: record the order and send the M-Pesa prompt. `409` with `detail.short` (each with substitutes) if something isn't available |
| `GET` | `/api/orders/{order_id}/payments/{payment_id}` | Order stage: `awaiting_payment`, `payment_failed`, `packing`, `ready`, `collected`, `on_the_way`, `delivered` or `cancelled`, with the receipt, and for deliveries the code, rider and status |
| `POST` | `/api/orders/{order_id}/pay` | Pay again while the order is still held: `{"method": "mpesa", "phone"}` or `{"method": "card", "email"}` |
| `GET` | `/api/orders/{order_id}/qr.svg` | QR code of the order number, for collection |

## Not yet

- **Notifications:** the customer keeps the page open, or comes back to it, to see when the order is ready. An SMS when it's packed would be the next step.
- **Product photos.**

## Tests

```bash
pytest
```

The tests use fake Inventory and Payments services and a temporary shelf event log.
