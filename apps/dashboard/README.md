# Store Dashboard

Four pages for store staff: **Overview**, **Products**, **Online orders** and **Exit check**.

**Who can do what.** Behind the proxy, each person signs in with their own login (`scripts/staff.sh`), and the proxy passes their name and role (`X-Staff-User`, `X-Staff-Role`):
- **Staff** can see everything, restock, and do exit checks.
- **Managers** can also add products, change prices, and record stock counts.

The server enforces this, and the pages hide what someone can't do. Restocks, counts, price changes and exit checks are recorded under the signed-in name. Run directly without the proxy (development), the dashboard acts as a manager and asks for a name where one is needed.

## Overview

Refreshed every 5 seconds:

- **Today** — paid sales, items sold, sales awaiting M-Pesa payment, low stock, open differences, shelf alerts.
- **Shelves** — each zone with the products assigned to it, their committed stock, and the last shelf event.
- **Needs attention** — misplaced items and events the Shelf Service flagged for review.
- **Stock** — committed stock per product (from the ledger), with low stock highlighted.
- **Differences with retailer's system** — open discrepancies and how far retailer sync is behind. Unknown differences have a form to record a shelf count, which becomes a stock adjustment.
- **Recent sales** and **Shelf activity** feeds.

## Products

- **Restock:** scan a delivered product's barcode, enter the quantity, save. Each restock is counted once even if the connection drops and the page retries.
- **Add or edit a product:** barcode, name and shelf price. Scanning a barcode the shelf sensors already know fills in the name and keeps the same product code, so the Overview's shelf zones line up. Click a product in the list to edit it.
- **Prices** set here are what Scan & Go charges; changes reach it within 30 seconds. A product with no price can't be bought with Scan & Go.

## Online orders

Paid orders from the online shop (`apps/storefront`), oldest first, refreshed every 10 seconds. Each order shows the customer's name and each item's shelf zone (or "Store room" when it isn't on a sensor shelf). Any staff member can press **Packed**. That's recorded under their name.

- **Collections:** the customer's page changes to **Ready to collect**. The order is handed over on the **Exit check** page, which shows it as an online order with who packed it.
- **Deliveries** (when `DISPATCH_URL` is set): the card shows the area, address and directions.
  - Staff pick a rider and press **Assign**. Riders on shift can also accept jobs themselves on their phone.
  - Once the order is packed and has a rider, **Hand to …** records the parcel going out with that rider.
  - The order then moves to **Out for delivery** until the rider enters the customer's code.
  - If the rider reports a problem, or the code locks after wrong tries, the card turns red with the customer's phone number. Staff call the customer, then press **Rider tries again** (or **Unlock the code**).
  - The **Exit check** refuses delivery orders, so one can't be handed to someone at the counter by mistake.
  - The **Riders** list shows who has signed in, who's on shift and how many jobs each has.

## Exit check

Staff scan the QR code on the customer's Scan & Go pass (a handheld 2D scanner types it and presses Enter), or type the 8-character code shown under PAID. The page shows:

- **PAID ✓** with the items, so staff can check the bag, and a **Confirm** button that records who checked it and when
- **PASS ALREADY USED** if that order already left the store, with when and who checked it
- **NOT PAID** if the order is unpaid or cancelled
- **NOT FOUND** for an unknown code

Once confirmed, the pass on the customer's phone turns grey (**CHECKED OUT**) within a few seconds.

Shelf events shown here are provisional. Stock only changes at checkout, restock, or a recorded count (see [`docs/sensor-logic.md`](../../docs/sensor-logic.md)).

## Where the data comes from

| Data | Source |
|---|---|
| Stock, products, sales, sync status, discrepancies | Inventory Service over HTTP (`INVENTORY_URL`) |
| Shelf events | The Shelf Service's JSON-lines log (`SHELF_EVENTS_PATH`, the file `python -m shelf.app --out` writes) |
| Zones and product names | The Shelf Service catalogue (`SHELF_CONFIG_PATH`) |

The dashboard runs at the store next to the Shelf Service. Each source is read separately, so if the Inventory Service is unreachable the shelf view keeps working, and a banner says what is missing.

## Run

```bash
cd apps/dashboard
pip install -r requirements-dev.txt
cp .env.example .env   # then edit; or export the variables directly
STORE_ID=001 INVENTORY_URL=http://localhost:8010 \
SHELF_EVENTS_PATH=../../services/shelf/shelf_events.jsonl \
SHELF_CONFIG_PATH=../../services/shelf/config.example.json \
uvicorn dashboard.app:create_app --factory --port 8020
```

Open http://localhost:8020.

| Setting | Default | Meaning |
|---|---|---|
| `STORE_ID` | `001` | Store to show |
| `STORE_TZ` | `Africa/Nairobi` | Time zone for "today" |
| `INVENTORY_URL` | `http://localhost:8010` | Inventory Service |
| `SHELF_EVENTS_PATH` | `shelf_events.jsonl` | Shelf Service event log |
| `SHELF_CONFIG_PATH` | `config.example.json` | Shelf catalogue (products and zones) |
| `LOW_STOCK_THRESHOLD` | `5` | Committed stock at or below this is flagged |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The dashboard page |
| `GET` | `/api/overview` | Everything the page shows, as JSON |
| `POST` | `/api/discrepancies/{id}/count` | Record a staff count (passed to the Inventory Service) |
| `GET` | `/products`, `/orders`, `/exit` | The Products, Online orders and Exit check pages |
| `GET` | `/api/me` | Who is signed in, and their role |
| `GET` | `/api/products` | Products with prices and stock |
| `PUT` | `/api/products/{product_id}` | Add or update a product and its price |
| `POST` | `/api/restocks` | Add stock from a barcode scan |
| `GET` | `/api/shelf-products` | Products the shelf catalogue knows, to reuse their codes |
| `GET` | `/api/exit/lookup?code=` | Find an order from its pass code |
| `POST` | `/api/exit/{sale_id}` | Record that the order left the store |
| `GET` | `/api/orders` | Paid online orders still in the store, orders out for delivery, and the riders, with shelf zones |
| `POST` | `/api/orders/{sale_id}/ready` | Mark an online order packed |
| `POST` | `/api/orders/{sale_id}/assign`, `/unassign`, `/hand-over`, `/retry` | Deliveries: give the job to a rider (`{"rider_id"}`), take it back, hand the parcel over, send the rider again |

## Not yet

- Shelf alerts can't be acknowledged yet; they drop off as newer events arrive.
- The exit check uses a handheld scanner or typed code; there's no in-browser camera scanner on this page yet.
- One store per dashboard instance.

## Tests

```bash
pytest
```
