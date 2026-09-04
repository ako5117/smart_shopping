# Architecture — Phase 1 (Shelf Visibility + Scan & Go)

## System overview

```mermaid
graph TD
    subgraph Store["In-Store"]
        EX[Per-Slot Entry/Exit<br/>Sensors]
        BR[Barcode Scanner<br/>at Restock]
    end

    subgraph Backend["Backend Services"]
        IS[Inventory Service]
        PS[Product/Pricing Service]
        DB[(Shared Product &<br/>Inventory Database)]
        PAY[Payment Gateway<br/>M-Pesa Daraja / Card]
        TAX[Tax/Receipt Service<br/>KRA eTIMS]
    end

    subgraph Customer["Customer-Facing"]
        APP[Scan & Go<br/>Mobile App]
    end

    subgraph Future["Phase 2 — not built yet"]
        WEB[Online Storefront]
        DISP[Rider Dispatch]
    end

    EX -->|slot in/out events| IS
    BR -->|restock scans, slot-to-SKU mapping| IS
    IS --> DB
    PS <--> DB
    APP -->|scan item| PS
    APP -->|running total| PS
    APP -->|checkout| PAY
    PAY -->|payment confirmed| IS
    PAY -->|transaction complete| TAX
    TAX -->|eTIMS receipt| APP

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
    participant TAX as Tax/Receipt Service

    C->>PS: Scan item barcode
    PS->>DB: Look up product + price
    DB-->>PS: Product details
    PS-->>C: Add to cart, update running total
    Note over C: Repeat per item

    C->>PAY: Checkout (M-Pesa Daraja or card)
    PAY-->>C: Payment confirmed
    PAY->>IS: Notify sale completed
    IS->>DB: Decrement stock for scanned items
    PAY->>TAX: Notify transaction complete
    TAX-->>C: eTIMS-compliant receipt issued
```

## Component notes

- **Per-Slot Entry/Exit Sensors → Inventory Service:** each shelf slot (one product per slot, per the data model) reports item-passed-through events via IR break-beam or time-of-flight sensing. This replaces whole-shelf weight sensing, which breaks down when a shelf mixes multiple SKUs with similar or overlapping weights — common on Kenyan supermarket shelves. Because the slot's product identity is already known (see below), the sensor only needs to count movement, not infer *what* moved.
- **Barcode Scanner at Restock:** establishes and confirms the slot-to-SKU mapping the entry/exit sensors rely on — restock scans are the ground truth for "this slot holds this product."
- **Shared Product/Inventory Database:** single source of truth for both Scan & Go and (later) the online storefront — this is why the data model has to be right before either feature is built (see `data-model.md`).
- **Payment Gateway:** kept as a separate integration boundary — M-Pesa via Daraja (STK Push) and card processing sit behind this abstraction so either can be added, swapped, or run in parallel without touching inventory logic.
- **Tax/Receipt Service:** listens for completed transactions and generates a KRA eTIMS-compliant receipt automatically. Directly extends Awesomtech's original founding concept (automated digital receipt curation for tax filing) into a live feature of Smart Shopping, not a separate product.
- **Phase 2 components (dashed):** included here only to show where they'll attach, not being built yet.
