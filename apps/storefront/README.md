# Online shop

Customers order online, pay with M-Pesa, and collect from the store or have a rider deliver. This is Phase 2: the shop reads live shelf data to show what's really available, and suggests substitutes when something runs low or sells out.

1. **Browse.** Products with prices, grouped by category, with search. Each product shows **In stock**, **Only N left** or **Out of stock**, refreshed every 15 seconds.
2. **Substitutes.** A sold-out product shows up to three in-stock products from the same category, closest in price first, each with an **Add** button. They also appear when the customer already has all that's left of a product in their basket.
3. **Pay.** The customer gives their name and phone number, and pays with **M-Pesa** or by **card** (when the Payments Service has cards on). The shop records the order in the Inventory Service, which holds the items. For M-Pesa, the Payments Service then sends the prompt to the phone. For a card, the customer goes to the card provider's payment page and comes back to the shop, which confirms the payment. If it fails, or they leave without paying, they can try again either way.
4. **Packing.** Paid orders appear on the dashboard's **Online orders** page. It shows the shelf zone for each item, or "Store room" for items not on a sensor shelf. Staff press **Packed: ready for collection**, and the customer's page changes to **Ready to collect**.
5. **Collect.** The customer shows the pass (code and QR) at the counter. Staff check it on the dashboard's **Exit check** page, which shows it as an online order for that customer's name. Once confirmed, the pass turns grey and reads **Collected**.

## Delivery

When the Dispatch Service is set (`DISPATCH_URL`), checkout also offers **Deliver to me**.

- **At checkout:** the customer picks their area (each has its own fee, from the Dispatch Service), types the address and directions, and can tap **Share my location** so the rider gets a map pin. The delivery fee is added to the order in the Inventory Service and is part of what M-Pesa charges.
- **Order page:** shows the customer's **4-digit delivery code** as soon as the order is paid. It goes **Paid → Packing → On the way → Delivered**. Once a rider has the job, it shows their name with a **Call** button.
- **At the door:** the customer gives the rider the code, and the order page changes to **Delivered**. Without the code the rider can't mark it delivered.
- **Problems:** if the rider reports a problem, the page says the store will call the customer.
- **Delivery unavailable:** if the Dispatch Service is down, **Deliver to me** isn't offered. If it fails at the moment of ordering, the order is cancelled before any payment request and its items are released.

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

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The shop |
| `GET` | `/api/store` | Store name, how long orders are held, and the delivery areas and fees |
| `GET` | `/api/catalogue` | Products with price, category, status (`in` / `few` / `out`), how many can be ordered, and substitutes |
| `POST` | `/api/orders` | `{"name", "phone", "items": [{"product_id", "qty"}]}`, plus `"payment_method": "card"` and `"email"` to pay by card (the answer then has a `checkout_url`), plus `"fulfilment": "delivery"` and `"delivery": {"area", "address", "notes", "lat", "lng"}` for a delivery: record the order and send the M-Pesa prompt. `409` with `detail.short` (each with substitutes) if something isn't available |
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
