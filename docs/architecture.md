# Architecture — Phase 1

## System overview

```mermaid
graph TD
    subgraph Shelf["In-store: shelf node"]
        LC["Load cells + HX711<br/>one per shelf zone"]
        ESP["ESP32<br/>sampling, filtering, settle detection"]
        CAM["ESP32-CAM<br/>zone snapshots"]
        LC --> ESP
    end

    subgraph Site["In-store: site services"]
        MQ["MQTT broker"]
        SS["Shelf Service<br/>weight + image fusion"]
        LS[("Local data store<br/>outbox queue")]
        BR["Barcode scanner<br/>restock"]
    end

    subgraph Cloud["Backend"]
        INV["Inventory Service<br/>stock ledger"]
        PP["Product / Pricing Service<br/>EAN-13 catalogue"]
        PAY["Payments Service<br/>M-Pesa Daraja"]
        TAX["Tax / Receipt Service<br/>eTIMS placeholder"]
        ADP["Retailer adapter<br/>REST"]
        DB[("Shared database")]
    end

    POS["Retailer POS / inventory system"]
    APP["Checkout app / store dashboard"]
    DAR["Safaricom Daraja"]

    ESP -->|weight events| MQ
    MQ --> SS
    CAM -->|image on request| SS
    SS -->|provisional shelf events| LS
    BR -->|restock scans| LS
    LS -->|sync when online| INV
    INV <--> DB
    PP <--> DB
    APP --> PP
    APP -->|pay| PAY
    PAY <-->|STK Push + callback| DAR
    PAY -->|payment confirmed| INV
    PAY -.->|sale completed| TAX
    INV <--> ADP
    ADP <-->|REST, not real-time| POS

    style TAX stroke-dasharray: 5 5
```

## Checkout flow

```mermaid
sequenceDiagram
    participant C as Customer / cashier app
    participant PP as Product/Pricing
    participant PAY as Payments Service
    participant D as Daraja
    participant INV as Inventory Service
    participant ADP as Retailer adapter

    C->>PP: Scan EAN-13 barcodes
    PP-->>C: Items and running total
    C->>PAY: Pay with M-Pesa (phone, amount)
    PAY->>D: STK Push request
    D-->>PAY: CheckoutRequestID (pending)
    Note over D: Customer enters M-Pesa PIN
    D->>PAY: Callback with result
    PAY-->>C: Paid / failed / cancelled
    PAY->>INV: Payment confirmed
    INV->>INV: Commit sale to stock ledger
    INV->>ADP: Queue sale for retailer system
```

## Shelf event flow

```mermaid
sequenceDiagram
    participant ESP as ESP32 (load cells)
    participant SS as Shelf Service
    participant CAM as ESP32-CAM
    participant LS as Local store

    ESP->>SS: Weight change settled (zone, before, after)
    SS->>CAM: Request snapshot of zone
    CAM-->>SS: JPEG
    SS->>SS: Match weight delta to candidate products, classify image
    SS->>LS: Shelf event: pick / return / moved / misplaced / anomaly
    Note over LS: Provisional only — stock changes at checkout and restock
```

## Component notes

- **Shelf node:** each shelf zone sits on its own load cell. The ESP32 samples, filters and detects when the weight has changed and settled, then publishes a weight event over MQTT. It does not identify products. See [`sensor-logic.md`](sensor-logic.md).
- **ESP32-CAM:** captures a snapshot of the zone when the Shelf Service asks. Image classification runs server-side, not on the ESP32.
- **Shelf Service:** fuses the weight change with the image result into a shelf event. Runs at the site so it keeps working during internet outages.
- **Local data store:** holds shelf events, restock scans and outgoing updates in an outbox until they are confirmed delivered. Every record carries an idempotency key so retries never double-count.
- **Inventory Service:** owns the stock ledger. Stock only changes on restock, sale, or an approved adjustment.
- **Product / Pricing Service:** catalogue keyed by EAN-13 barcode, including unit weight and the label the image classifier uses.
- **Payments Service:** M-Pesa STK Push through Daraja. Card payments in Phase 2. See [`services/payments`](../services/payments).
- **Tax / Receipt Service:** placeholder. Receives completed sales and will issue eTIMS e-receipts once the integration route is confirmed. A sale is never blocked by a receipt failure.
- **Retailer adapter:** REST connection to the store's existing POS/inventory. Their systems are often slow, so sync is asynchronous. See [`reconciliation.md`](reconciliation.md).

## Deployment (POC)

- Cloud services on DigitalOcean (shared company account).
- Site services (MQTT broker, Shelf Service, local store) on one small machine in the store; for the bench prototype, a laptop.
- Daraja callbacks need a public HTTPS URL, so the Payments Service runs in the cloud.
