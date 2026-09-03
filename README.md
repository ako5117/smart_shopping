# Smart Shopping — Awesomtech

Digitizing retail shelves so customers can browse, order, and pay remotely, starting with in-store shelf visibility and a phone-based "Scan & Go" flow, later extending to full remote/online ordering with rider dispatch.

## Team

| Name | Role |
|---|---|
| Eng. Elisha Akech | Director — strategic direction, partnerships |
| Adrian Odira | System Design Lead — architecture, workflows, hardware/software mapping |
| Wayne Oguda | Process/automation design, engineering execution, infrastructure |
| Mirean Wanjau | Data analytics, ML/AI, administration |

## Build Order (Phased)

1. **Shelf Visibility** (Phase 1 foundation) — weight-sensor shelves + barcode scanning at restock, feeding a live inventory data model.
2. **Scan & Go** (Phase 1 continued) — customer's own phone: scan items as they shop, running total, pay in-app. Built on the same product/inventory data as step 1.
3. **Online + In-Store Sync** (Phase 2) — online storefront reads the live shelf-visibility feed to show real availability and substitutes; connects to rider dispatch for remote orders.

See [`docs/roadmap.md`](docs/roadmap.md) for the full phase breakdown and [`docs/architecture.md`](docs/architecture.md) for system diagrams.

## Repo Structure

```
smart-shopping/
├── README.md
├── docs/
│   ├── roadmap.md         # Phased plan, sequencing rationale
│   ├── architecture.md    # System architecture (Mermaid diagrams)
│   └── data-model.md      # Shared product/inventory data model (Mermaid ER diagram)
├── services/               # (to be added) backend services per component
├── apps/                   # (to be added) Scan & Go mobile app, admin dashboard
└── hardware/                # (to be added) shelf-sensor firmware/integration notes
```

`services/`, `apps/`, and `hardware/` are placeholders — added as each component moves from design to build.

## Tooling

- **Version control:** GitHub (this repo)
- **Diagrams:** Mermaid, kept in `/docs` as version-controlled text, not external files
- **Hosting (POCs):** DigitalOcean
- **AI-assisted development:** Claude / Claude Code

## Status

🟡 Design phase — architecture and data model in review before first build sprint.
