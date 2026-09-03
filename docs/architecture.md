# Architecture — Phase 1 (Shelf Visibility + Scan & Go)

## System overview

```mermaid
graph TD
    subgraph Store["In-Store"]
        WS[Weight-Sensor Shelves]
        BR[Barcode Scanner<br/>at Restock]
    end

    subgraph Backend["Backend Services"]
        IS[Inventory Service]
        PS[Product/Pricing Service]
        DB[(Shared Product &<br/>Inventory Database)]
        PAY[Payment Gateway<br/>Integration]
    end

    subgraph Customer["Customer-Facing"]
        APP[Scan & Go<br/>Mobile App]
    end

    subgraph Future["Phase 2 — not built yet"]
        WEB[Online Storefront]
        DISP[Rider Dispatch]
    end

    WS -->|weight delta events| IS
    BR -->|restock scans| IS
    IS --> DB
    PS <--> DB
    APP -->|scan item| PS
    APP -->|running total| PS
    APP -->|checkout| PAY
    PAY -->|payment confirmed| IS

    WEB -.->|reads live stock| DB
    WEB -.-> DISP

    style Future fill:#f5f5f5,stroke:#999,stroke-dasharray: 5 5
```

## Scan & Go flow (sequence)

```mermaid
sequenceDiagram
    participant C as Customer (App)
    participant PS as Product/Pricing Service
    participant DB as Inventory DB
    participant PAY as Payment Gateway
    participant IS as Inventory Service

    C->>PS: Scan item barcode
    PS->>DB: Look up product + price
    DB-->>PS: Product details
    PS-->>C: Add to cart, update running total
    Note over C: Repeat per item

    C->>PAY: Checkout (pay in-app)
    PAY-->>C: Payment confirmed
    PAY->>IS: Notify sale completed
    IS->>DB: Decrement stock for scanned items
```

## Component notes

- **Weight-Sensor Shelves → Inventory Service:** each shelf reports weight-delta events; Inventory Service infers quantity change per SKU (calibrated per product weight at restock).
- **Barcode Scanner at Restock:** ground-truth correction point — restock scans confirm *which* SKU was added, compensating for the weight sensor's blind spot on product identity.
- **Shared Product/Inventory Database:** single source of truth for both Scan & Go and (later) the online storefront — this is why the data model has to be right before either feature is built (see `data-model.md`).
- **Payment Gateway:** kept as a separate integration boundary — swappable (M-Pesa, card, etc.) without touching inventory logic.
- **Phase 2 components (dashed):** included here only to show where they'll attach, not being built yet.
