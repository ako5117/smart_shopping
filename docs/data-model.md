# Shared Data Model

The single data model all Phase 1 features sit on. Mirean owns the detailed sensor data points; this model sets the entities they attach to.

```mermaid
erDiagram
    STORE ||--o{ SHELF : contains
    SHELF ||--o{ SHELF_ZONE : "divided into"
    SHELF_ZONE ||--o{ ZONE_ASSIGNMENT : "planned to hold"
    PRODUCT ||--o{ ZONE_ASSIGNMENT : "assigned to"
    SHELF_ZONE ||--o{ SHELF_EVENT : reports
    PRODUCT o|--o{ SHELF_EVENT : "identified as"
    PRODUCT ||--o{ STOCK_LEDGER : "stock tracked by"
    STORE ||--o{ STOCK_LEDGER : holds
    CART_SESSION ||--o{ CART_ITEM : contains
    PRODUCT ||--o{ CART_ITEM : "scanned as"
    CART_SESSION ||--o| SALE : "settles into"
    SALE ||--o{ PAYMENT : "paid by"
    SALE ||--o| RECEIPT : generates
    STOCK_LEDGER ||--o| OUTBOX : "synced via"
    PRODUCT ||--o{ DISCREPANCY : "flagged for"

    STORE {
        string store_id PK
        string name
        string location
    }
    SHELF {
        string shelf_id PK
        string store_id FK
        string camera_id
    }
    SHELF_ZONE {
        string zone_id PK
        string shelf_id FK
        int load_cell_channel
        float calibration_factor
        float noise_threshold_g
    }
    ZONE_ASSIGNMENT {
        string zone_id FK
        string product_id FK
        int expected_qty
    }
    PRODUCT {
        string product_id PK
        string ean13 "unique"
        string name
        decimal unit_price
        float unit_weight_g
        float weight_tolerance_g
        string classifier_label
    }
    SHELF_EVENT {
        string event_id PK
        string zone_id FK
        string product_id FK "null if unidentified"
        string event_type "pick | return | moved | misplaced | anomaly"
        int qty
        float weight_delta_g
        float confidence
        string image_ref
        datetime occurred_at
    }
    STOCK_LEDGER {
        string entry_id PK
        string store_id FK
        string product_id FK
        string entry_type "restock | sale | return | adjustment"
        int qty_change
        string reason
        string source_ref "sale_id, scan_id, discrepancy_id"
        string idempotency_key "unique"
        datetime occurred_at
    }
    CART_SESSION {
        string session_id PK
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
    SALE {
        string sale_id PK
        string session_id FK
        decimal total_amount
        string status "pending_payment | paid | cancelled"
        datetime completed_at
    }
    PAYMENT {
        string payment_id PK
        string sale_id FK
        string method "mpesa | card"
        string status "pending | paid | failed | cancelled"
        int amount
        string phone_number
        string checkout_request_id "Daraja"
        string mpesa_receipt_number "Daraja"
        int result_code
        datetime updated_at
    }
    RECEIPT {
        string receipt_id PK
        string sale_id FK
        string status "pending | issued | failed"
        string external_invoice_no
        string verification_url
    }
    OUTBOX {
        string outbox_id PK
        string entry_id FK
        string destination "retailer_pos"
        string status "queued | sent | confirmed | failed"
        int attempts
        datetime next_attempt_at
    }
    DISCREPANCY {
        string discrepancy_id PK
        string store_id FK
        string product_id FK
        int our_qty
        int retailer_qty
        string category "timing | known_cause | unknown"
        string status "open | resolved"
    }
```

## Notes

- **Two kinds of stock record.** `SHELF_EVENT` is what the sensors saw (provisional, may be wrong). `STOCK_LEDGER` is committed stock, written only on restock, sale, return, or an approved adjustment. Current stock is the sum of ledger entries per product and store — never a separately edited number.
- **Weight and identity live on `PRODUCT`.** `unit_weight_g` and `weight_tolerance_g` let the Shelf Service turn a weight change into a quantity; `classifier_label` links the product to the image classifier.
- **`ZONE_ASSIGNMENT`** is the planogram: which products are meant to be in which zone. A zone can hold more than one product; the camera resolves which one moved.
- **`idempotency_key`** on ledger entries means a retried sync or a repeated callback can never count a sale twice.
- **`PAYMENT` is separate from `SALE`** so a sale can have a failed attempt followed by a successful one, and card payments slot in later without schema changes.
- **`RECEIPT`** is a placeholder for eTIMS. Fields will be finalised once the integration route is confirmed.
- **`OUTBOX` and `DISCREPANCY`** support retailer sync and reconciliation — see [`reconciliation.md`](reconciliation.md).
