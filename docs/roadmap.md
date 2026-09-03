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
| Weight-sensor shelves | Low | Stock count only — can't tell *which* product if items are swapped | **Pilot choice** |
| RFID tags per item | High (per-unit) | SKU-level accurate | Phase 2+ candidate |
| Computer vision (shelf cameras) | Medium-high (setup/compute) | Most flexible | Phase 2+ candidate |

**Pilot approach:** weight sensors + barcode scanning at restock. Cheapest path to real data flowing.

## Phase 1 (continued) — Scan & Go

**Goal:** scan → running total → pay, as a single mobile flow (not separate cart + checkout systems).

- App-based (customer's own phone), not dedicated cart hardware — far cheaper POC.
- Depends on the same product/pricing data model as Shelf Visibility.

## Phase 2 — Online + In-Store Sync

**Goal:** online storefront shows real shelf availability, with substitute recommendations when out of stock.

- Requires Phase 1's live inventory feed — without it, "available/substitute" data isn't real.
- Extends to rider dispatch for full remote ordering (the original "shop from your bedroom" vision from the first team meeting).

## Before writing code

- [ ] Agree one shared product/inventory data model that all features sit on top of (see `data-model.md`)
- [ ] Pick one pilot/demo store + rough hardware budget — decides the shelf-sensing route
- [ ] Confirm hosting (DigitalOcean) and repo conventions with Wayne
