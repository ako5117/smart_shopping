# Roadmap

## Sequencing rationale

The four original feature ideas (electronic shelving, smart carts, smart checkout, online/in-store integration) collapse into three build phases, because #2 and #3 are really one feature, and #4 depends on #1's data existing first.

```
Shelf Visibility (1) → Scan & Go (2 + 3) → Online/In-Store Sync (4)
```


## Phase 1 — Shelf Visibility (inventory foundation)

**Goal:** real "what's on shelf vs. in store" data, flowing live.

Hardware routes considered:

| Route | Cost | Accuracy | Notes |
|---|---|---|---|
| Weight-sensor shelves | Low | Breaks down on multi-product shelves — a weight delta can't identify *which* SKU moved if items have similar/overlapping weights | Not recommended where shelves are mixed |
| **Per-slot entry/exit sensors (IR break-beam / ToF)** | Low-medium | Counts items passing through a known slot, not weight-inferred — works regardless of product weight overlap, since slot→product mapping is already known from restock scans | **Pilot choice** |
| RFID tags per item | High (per-unit) | SKU-level accurate | Phase 2+ candidate |
| Computer vision (shelf cameras) | Medium-high (setup/compute) | Most flexible | Phase 2+ candidate |

**Pilot approach (revised):** per-slot entry/exit sensors + barcode scanning at restock. Kenyan supermarket shelves commonly mix multiple SKUs on one shelf, which breaks whole-shelf weight sensing — slot-level entry/exit detection sidesteps that by counting movement per known slot instead of inferring product identity from a weight number. Restock scans still provide the ground-truth slot→product mapping. Known trade-off: break-beam sensors can miscount on ambiguous hand motions (browsing without removing, grabbing multiple items in one pass) — needs debounce/calibration tuning, similar in kind to the weight sensor's own blind spot, just a different failure mode.

## Phase 1 (continued) — Scan & Go

**Goal:** scan → running total → pay, as a single mobile flow (not separate cart + checkout systems).

**Pilot approach:** app-based (customer's own phone), not dedicated cart hardware — far cheaper POC.
- Depends on the same product/pricing data model as Shelf Visibility.
- **Payment methods:** M-Pesa (Daraja API, STK Push) and card, both behind a single Payment Gateway abstraction — swappable/extendable without touching inventory logic.
- **Tax compliance:** each completed transaction auto-generates a KRA eTIMS-compliant receipt. This connects directly back to Awesomtech's original founding idea (automated digital receipt curation for tax filing) — Scan & Go becomes a live proof of that thesis, not just a checkout feature.

## Phase 2 — Online + In-Store Sync

**Goal:** online storefront shows real shelf availability, with substitute recommendations when out of stock.

- Requires Phase 1's live inventory feed — without it, "available/substitute" data isn't real.
- Extends to rider dispatch for full remote ordering (the original "shop from your bedroom" vision from the first team meeting).

## Before writing code

- [ ] Agree one shared product/inventory data model that all features sit on top of (see `data-model.md`)
- [ ] Pick one pilot/demo store + rough hardware budget — decides the shelf-sensing route
- [ ] Confirm hosting (DigitalOcean) and repo conventions with Wayne
