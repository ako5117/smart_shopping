# Roadmap

## Phase 1 — Shelf visibility and checkout

### Shelf visibility

**Goal:** know what is on each shelf zone in real time, and which product moved.

**Sensing approach (agreed 22 Sep 2026):** a hybrid of two sensor types.

| Sensor | Role | Strength | Weakness covered by the other |
|---|---|---|---|
| Load cells (weight) per shelf zone | Detect that items were removed or added, and how many | Counts two items picked together correctly (2 × unit weight) | Cannot tell apart products with similar weights |
| Camera + image classification | Confirm which product moved, and whether it belongs on that shelf | Identifies products regardless of weight | Weak at counting; affected by occlusion and lighting |

Full logic: [`sensor-logic.md`](sensor-logic.md).

**Earlier options considered:** whole-shelf weight only (fails on mixed shelves), per-slot entry/exit beams (miscounts multi-item picks), RFID per item (per-unit cost too high for Phase 1). Entry/exit beams remain an optional extra signal if bench testing shows a gap.

**Inventory rule:** shelf sensor events are *provisional*. Stock is only committed at checkout (sale) and at restock (barcode scan). An item picked up and put back never touches stock.

**Product identity:** international EAN-13 barcodes.

### Checkout

- **M-Pesa** via the Safaricom Daraja API (STK Push) — Phase 1. Service: [`services/payments`](../services/payments).
- **Card payments** — Phase 2.
- **eTIMS e-receipts** — placeholder in the architecture. Integration route under review (third-party gateway documentation received).
- **Self-checkout trust:** exit check (staff or receipt scan) assumed for supermarket pilots. Lower-risk environments such as airport duty-free shops are the preferred first market.

### Retailer integration

- Retailers will not replace their POS/inventory systems. Smart Shopping connects to them through a REST API adapter.
- A local data store at each site queues outgoing updates so nothing is lost when the connection or the retailer's system is slow or down.
- Our counts and the retailer's will sometimes differ. Handling: [`reconciliation.md`](reconciliation.md).

## Phase 2 — Online + in-store sync

- Online storefront reads live shelf data to show real availability and suggest substitutes.
- Rider dispatch for remote orders.
- Card payments.

## Later — Warehouse / logistics

The same sensing core tracks stock in warehouses and go-downs (including retailers' own supply warehouses). A phone app for stock controllers replaces manual shelf counts.

## Target markets

1. High-end, low-theft retail — airport duty-free shops.
2. Supermarkets and minimarts (with exit checks).
3. Warehouses and go-downs (internal use).

## Before and during the build

- [x] Agree the data model all features sit on (see `data-model.md`)
- [x] Agree the development workflow (see `CONTRIBUTING.md`)
- [ ] Create shared company DigitalOcean and GitHub accounts; move this repo into the company organisation
- [ ] Build and test the bench prototype (see `hardware/bench-prototype.md`)
- [ ] Choose barcode/inventory open-source libraries
- [ ] Define sensor data points (Mirean)
- [ ] Confirm eTIMS integration route
- [ ] Identify a pilot site
