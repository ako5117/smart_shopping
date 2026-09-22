# Smart Shopping — Awesomtech

Digitizing retail shelves so stores know what is on each shelf in real time, and customers can pay without queuing. Phase 1 covers shelf visibility and M-Pesa checkout. Later phases extend to online ordering with rider dispatch, and to warehouse stock tracking.

## Team

| Name | Role |
|---|---|
| Eng. Elisha Akech | Director — strategic direction, partnerships |
| Adrian Odira | System Design Lead — architecture, sensor logic, payments |
| Wayne Oguda | Engineering execution, infrastructure, barcode/inventory libraries |
| Mirean Wanjau | Data (sensor data structure, analytics, ML), administration |

## Build Order (Phased)

1. **Shelf Visibility** (Phase 1) — load-cell (weight) sensing per shelf zone, verified by camera-based product recognition. Products identified by EAN-13 barcode. Stock is committed at checkout and restock, not when an item is picked up.
2. **Checkout** (Phase 1) — M-Pesa via the Daraja API (STK Push). Card payments follow in Phase 2. eTIMS e-receipts are a placeholder until the integration route is confirmed.
3. **Retailer integration** (Phase 1–2) — REST API connection to the store's existing POS/inventory system, with a local data store so nothing is lost during outages, and reconciliation of count differences.
4. **Online + in-store sync, rider dispatch** (Phase 2+).
5. **Warehouse / go-down stock tracking** (later) — same sensing core, internal logistics use case.

See [`docs/roadmap.md`](docs/roadmap.md) for the phase breakdown and [`docs/architecture.md`](docs/architecture.md) for system diagrams.

## Repo Structure

```
smart_shopping/
├── README.md
├── CONTRIBUTING.md          # Branching, PRs, commit conventions
├── docs/
│   ├── roadmap.md           # Phased plan and decisions
│   ├── architecture.md      # System architecture (Mermaid)
│   ├── data-model.md        # Shared data model (Mermaid ER)
│   ├── sensor-logic.md      # Weight + optical sensing logic
│   └── reconciliation.md    # Keeping our counts and the retailer's in agreement
├── hardware/
│   └── bench-prototype.md   # Bench rig parts list, wiring, test plan
├── services/
│   └── payments/            # M-Pesa (Daraja STK Push) service — Python/FastAPI
└── apps/                    # (to be added) store dashboard, Scan & Go app
```

## Tooling

- **Version control:** GitHub — feature branches and pull requests, see [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **Backend:** Python
- **Hardware:** ESP32, programmed with Arduino
- **Diagrams:** Mermaid, kept in `/docs` as version-controlled text, not external files
- **Hosting (POCs):** DigitalOcean (shared company account — pending setup)
- **AI-assisted development if needed:** Claude / Claude Code

## Status

🟡 Phase 1 design agreed (22 Sep 2026). Payments service in development; bench prototype pending parts.
