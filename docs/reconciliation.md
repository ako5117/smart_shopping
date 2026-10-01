# Reconciliation — Keeping Our Counts and the Retailer's in Agreement

Smart Shopping runs alongside the retailer's existing POS/inventory system. The two will sometimes disagree (e.g. we count 29, they count 28). This document sets how that is handled.

## Which system is the source of truth

| Question | Source of truth |
|---|---|
| Official stock for accounting, orders, tax | **Retailer's system** |
| What is physically on the shelf right now | **Smart Shopping** |
| What was sold through Smart Shopping checkout | **Smart Shopping ledger**, pushed to the retailer |

We never silently overwrite the retailer's numbers, and they never silently overwrite ours. Differences are recorded and resolved.

## How data flows

1. Every committed change (restock, sale, return, adjustment) is written to our `STOCK_LEDGER` with a unique `idempotency_key`.
2. The same change is placed in the `OUTBOX` at the site.
3. The retailer adapter sends outbox items to the retailer's API. On failure it retries with increasing delay (30 s, 2 min, 10 min, then hourly).
4. An item is only marked `confirmed` when the retailer's system acknowledges it. Sending the same key twice must be harmless.
5. Periodically (start with hourly, and at close of day) the adapter pulls a stock snapshot from the retailer's system and compares it per product.

## Classifying a difference

When `our_qty ≠ retailer_qty` for a product, a `DISCREPANCY` is created and classified:

| Category | Test | Action |
|---|---|---|
| **Timing** | Outbox still has unconfirmed items for this product that explain the gap | Wait. Re-check after the outbox clears; auto-resolve if it matches. |
| **Known cause** | Gap matches a recorded event we can explain: a sale at the retailer's own till not seen by us, a delivery booked in their system before our restock scan, a damaged-goods write-off | Record the matching ledger entry with the reason code. |
| **Unknown** | None of the above | Open for review. A staff member does a quick count with the stock-controller app; the count becomes an `adjustment` entry in both systems. |

Small tolerances can be set per product category to avoid noise (e.g. ignore a gap of 1 on items sold loose), but every adjustment is still logged.

## Offline behaviour

- The site keeps working without internet: shelf events, restock scans and sales queue in the local store.
- When the connection returns, the outbox drains in order. Idempotency keys prevent duplicates.
- Reconciliation pauses while offline and runs a full comparison once the queue has cleared.

## Why this is also a product feature

Recurring unknown discrepancies per product, shelf or time of day point to shrinkage (theft, damage, mis-scans). Reporting this is part of the data value the system offers retailers and warehouses.

## eTIMS note

If the eTIMS route chosen keeps its own stock count and blocks sales above it, that becomes a third record to keep in step. To be addressed once the integration route is confirmed.

## Open questions

- What stock endpoints does each target retailer's system expose, and how often can we call them?
- Who at the store approves adjustments?
- Close-of-day comparison time per site.
