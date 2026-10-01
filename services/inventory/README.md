# Inventory Service

Owns committed stock. Stock only changes on a **restock** (barcode scan), a **sale** (committed once payment is confirmed), a **return**, or an **adjustment** with a reason. Current stock is always the sum of these ledger entries, never a number edited directly. Shelf sensor events never change stock; see [`docs/sensor-logic.md`](../../docs/sensor-logic.md).

## Checkout flow

1. The checkout app creates the sale with its items: `POST /sales` — stock is untouched.
2. It starts the M-Pesa payment through the Payments Service.
3. When the payment is confirmed, the Payments Service calls `POST /sales/{sale_id}/commit` and the items leave stock.

**Who did what.** Restocks and adjustments store `recorded_by`, and product updates log price changes with `changed_by`. Callers can name the person in the request; otherwise the signed-in staff member the proxy passes in `X-Staff-User` is used. Stock counts record the counter.

Each sale keeps the price of every item at the moment it was created (`unit_price_kes`), so changing a price later never changes an old receipt.

Every write is idempotent: a re-sent restock scan, a repeated commit, or a retried request is counted once.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/products` | Registered products |
| `PUT` | `/products/{product_id}` | Register a product, its EAN-13 barcode and its shelf price (`price_kes`, optional; leaving it out keeps the current price) |
| `GET` | `/products/{product_id}/price-history` | Every price change: old and new price, who, when |
| `POST` | `/restocks` | Add stock from a barcode scan (`scan_id` makes re-sends safe) |
| `GET` | `/stock/{store_id}` | Stock of every product in a store |
| `GET` | `/stock/{store_id}/{product_id}` | Stock of one product, with recent history |
| `GET` | `/sales?store_id=&limit=` | Most recent sales in a store, newest first |
| `POST` | `/sales` | Create a sale at checkout (before payment) |
| `GET` | `/sales/lookup?store_id=&code=` | Find an order from the code on a Scan & Go pass (its last 6+ characters, or the full id) |
| `POST` | `/sales/{sale_id}/exit` | Staff checked the pass at the exit: `{"checked_by": "Mary"}`. A pass works once; unpaid orders are refused |
| `POST` | `/sales/{sale_id}/commit` | Payment confirmed — take the items out of stock |
| `POST` | `/sales/{sale_id}/cancel` | Abandon an unpaid sale |
| `POST` | `/sales/{sale_id}/returns` | Return items from a paid sale (never more than were sold) |
| `POST` | `/adjustments` | Correct stock, with a reason (damage, count, etc.) |
| `POST` | `/reconcile` | Compare with the retailer's stock figures |
| `GET` | `/sync/{store_id}` | Movements still waiting to reach the retailer's system, and the last error |
| `GET` | `/discrepancies/{store_id}` | Open differences with the retailer's system |
| `POST` | `/discrepancies/{id}/count` | Staff counted the shelf — adjust and close |

## Retailer sync and reconciliation

Implements [`docs/reconciliation.md`](../../docs/reconciliation.md):

- Every ledger entry is queued in an outbox in the same transaction, and sent to the retailer's system in order. A failure pauses the queue and retries after 30 s, 2 min, 10 min, then hourly, so the retailer never receives changes out of order.
- Reconciliation compares our stock with the retailer's snapshot. A gap exactly explained by changes still in the outbox is **timing** and clears itself; anything else is **unknown** and waits for a staff count, which is recorded as an adjustment.
- `HttpRetailerAdapter` is a placeholder REST contract until a pilot retailer's actual API is known; a real integration implements `send()` and `stock_snapshot()`.

```bash
RETAILER_API_URL=https://retailer.example/api STORE_IDS=001 python -m inventory.worker
```

## Run and test

```bash
cd services/inventory
pip install -r requirements-dev.txt
uvicorn inventory.api:create_app --factory --port 8010
pytest
```

Storage is SQLite (`INVENTORY_DB_PATH`, default `inventory.db`) until the shared database is set up.
