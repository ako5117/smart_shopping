# Warehouse Service

Stock in the warehouse (go-down), per bin, and goods sent from it to the stores. Stock controllers work from a page on their phone at `/warehouse/`, scanning product barcodes and bin labels with the camera, so they don't count on paper.

Like the stores' stock ([`services/inventory`](../inventory)), warehouse stock is a ledger. It only changes when something happens to it, and stock in a bin is always the sum of those entries:

| Entry | When |
|---|---|
| `receive` | Goods in from a supplier, put away in a bin (with the supplier's delivery note) |
| `count` | A stock controller counted a product in a bin: the bin becomes that number, and the difference is kept with their name |
| `move` | Goods moved from one bin to another (refused if the first bin doesn't hold that many) |
| `transfer` | Goods sent to a store. They leave the warehouse at once and reach the store's stock when the store receives them |

Every change is recorded with the signed-in stock controller (`X-Staff-User` from the proxy). Every write carries an id the phone makes once, so a re-sent scan, count, move or transfer is counted once.

**Products** belong to the Inventory Service: a barcode has to be registered on the store dashboard before the warehouse can take it in. The warehouse keeps a copy of each product's name and barcode, so its stock lists still work if the Inventory Service is briefly down.

## Sending goods to a store

1. On **Send to store**, the stock controller picks the store, then scans each product and the bin it's taken from. Then they tap **Send**. It's all or nothing: if any bin is short, nothing is sent and the page says which bin.
2. When the goods arrive, **Received** adds them to the store's stock as a restock in the Inventory Service (one per product, recorded with the name of whoever tapped it). If the connection drops part-way, tapping **Received** again finishes the job without counting anything twice.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The stock controller's phone page |
| `GET` | `/api/me` | Who's signed in, the warehouse, and the stores it supplies |
| `GET` | `/api/products/{ean13}` | A product and how many of it are in each bin |
| `GET` | `/api/stock?bin=` | What's in each bin (or one bin) |
| `GET` | `/api/history?limit=` | The latest ledger entries |
| `POST` | `/api/receipts` | Goods in: `{ean13, qty, bin, ref, scan_id}` |
| `GET` / `POST` | `/api/counts` | Latest counts / record one: `{ean13, bin, counted_qty, count_id}` |
| `POST` | `/api/moves` | `{ean13, qty, from_bin, to_bin, move_id}` |
| `GET` | `/api/transfers?status=` | Transfers, newest first; `dispatched` or `received` |
| `POST` | `/api/transfers` | Send to a store: `{transfer_id, store_id, lines: [{ean13, bin, qty}]}`. A short bin is a `409` with `detail.short` |
| `GET` | `/api/transfers/{id}` | One transfer and its lines |
| `POST` | `/api/transfers/{id}/received` | The store has the goods: add them to its stock |

Bin codes are up to 20 letters, digits and dashes (e.g. `A-01-3`), printed on the racking as a Code 128 or QR label. Typing them works too.

## Run and test

```bash
cd services/warehouse
pip install -r requirements-dev.txt
INVENTORY_URL=http://localhost:8010 DEV_USER=wanjiru uvicorn warehouse.api:create_app --factory --port 8080
pytest
```

`DEV_USER` stands in for the login when running without the proxy. Logins for stock controllers: `./scripts/staff.sh add wanjiru --stock` (managers can use the page too).

| Setting | Default | Meaning |
|---|---|---|
| `WAREHOUSE_ID`, `WAREHOUSE_NAME` | `WH1`, `Warehouse` | Which warehouse, and the name in the page header |
| `WAREHOUSE_STORES` | `001` | Comma-separated ids of the stores it sends goods to |
| `INVENTORY_URL` | `http://localhost:8010` | Inventory Service (products, and restocks when a store receives a transfer) |
| `DATABASE_URL`, `DATABASE_SCHEMA` | | The shared PostgreSQL database and this service's schema (default `warehouse`) |
| `WAREHOUSE_DB_PATH` | `warehouse.db` | SQLite file used when `DATABASE_URL` isn't set |

`warehouse/sqldb.py` is the same storage layer as the other services and must stay an identical copy (a test in `services/inventory` checks). To run the tests against PostgreSQL as well, set `TEST_DATABASE_URL` as described in [`services/inventory/README.md`](../inventory/README.md).

## Not yet

- Receiving a transfer at the store takes the whole transfer. A short or damaged delivery is put right with a stock count at the store afterwards.
- One warehouse per deployment. Shelf sensors in the warehouse (the same sensing core as Smart Shelves) would add live bin activity on top of this ledger.
