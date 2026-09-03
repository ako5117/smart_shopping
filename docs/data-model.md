# Shared Product/Inventory Data Model

This is the single data model all Phase 1 (and later Phase 2) features sit on top of. Getting this right before writing code avoids rebuilding it once Scan & Go and online sync are layered on.

```mermaid
erDiagram
    STORE ||--o{ SHELF : contains
    SHELF ||--o{ SHELF_SLOT : has
    PRODUCT ||--o{ SHELF_SLOT : "stocked in"
    PRODUCT ||--o{ INVENTORY_EVENT : "tracked by"
    SHELF ||--o{ INVENTORY_EVENT : "reports"
    CART_SESSION ||--o{ CART_ITEM : contains
    PRODUCT ||--o{ CART_ITEM : "scanned as"
    CART_SESSION ||--|| TRANSACTION : "settles into"
    CUSTOMER ||--o{ CART_SESSION : starts

    STORE {
        string store_id PK
        string name
        string location
    }
    SHELF {
        string shelf_id PK
        string store_id FK
        string sensor_type "weight | rfid | vision"
    }
    SHELF_SLOT {
        string slot_id PK
        string shelf_id FK
        string product_id FK
        int expected_qty
    }
    PRODUCT {
        string product_id PK
        string sku
        string name
        decimal unit_price
        decimal unit_weight
    }
    INVENTORY_EVENT {
        string event_id PK
        string shelf_id FK
        string product_id FK
        string event_type "restock | weight_delta | sale_decrement"
        int qty_change
        datetime timestamp
    }
    CUSTOMER {
        string customer_id PK
        string phone_number
    }
    CART_SESSION {
        string session_id PK
        string customer_id FK
        string store_id FK
        string status "active | checked_out | abandoned"
    }
    CART_ITEM {
        string cart_item_id PK
        string session_id FK
        string product_id FK
        int quantity
        decimal price_at_scan
    }
    TRANSACTION {
        string transaction_id PK
        string session_id FK
        decimal total_amount
        string payment_status
        datetime completed_at
    }
```

## Notes for implementation

- `INVENTORY_EVENT` is the append-only ledger every stock change flows through — both the weight-sensor and barcode-restock paths write here, and the online storefront (Phase 2) reads current stock as a rollup of this table, not a separately maintained field.
- `SHELF.sensor_type` is deliberately per-shelf, not global — lets the pilot store mix weight sensors on fast-moving items with barcode-only on others, without a schema change later.
- `price_at_scan` on `CART_ITEM` is intentional: if pricing changes mid-shop, the customer pays what they saw when they scanned.
