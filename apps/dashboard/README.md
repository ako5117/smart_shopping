# Store Dashboard

One page for store staff, refreshed every 5 seconds:

- **Today** — paid sales, items sold, sales awaiting M-Pesa payment, low stock, open differences, shelf alerts.
- **Shelves** — each zone with the products assigned to it, their committed stock, and the last shelf event.
- **Needs attention** — misplaced items and events the Shelf Service flagged for review.
- **Stock** — committed stock per product (from the ledger), with low stock highlighted.
- **Differences with retailer's system** — open discrepancies and how far retailer sync is behind. Unknown differences have a form to record a shelf count, which becomes a stock adjustment.
- **Recent sales** and **Shelf activity** feeds.

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

## Not yet

- No login. Run it on the store network only until staff accounts exist.
- Shelf alerts can't be acknowledged yet; they drop off as newer events arrive.
- One store per dashboard instance.

## Tests

```bash
pytest
```
