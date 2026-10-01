# Scan & Go

The customer's phone becomes the till. They scan products as they shop, pay with M-Pesa, and show the receipt screen at the exit.

1. **Scan.** The phone camera reads EAN-13 barcodes, or the customer types the number. Each product is looked up and added to the basket with its price.
2. **Pay.** The customer enters their M-Pesa number. The app creates the sale in the Inventory Service, then the Payments Service sends an STK Push to the phone.
3. **Receipt.** When the payment is confirmed, the Payments Service commits the sale, so the items leave stock. The app shows a green **PAID** pass with the order code, the M-Pesa receipt number and a live clock, which makes an old screenshot easy to spot at the exit.

If the customer cancels or the payment fails, they can send the request again for the same order. Nothing leaves stock until a payment succeeds.

## How it stays safe

- **Prices come from the server.** The phone sends only products and quantities. The total is worked out here from the price list and sent to M-Pesa, so changing the page can't lower the price.
- **Order IDs are random** (`SG-` plus 16 hex characters). Looking up an order needs both its ID and its payment ID.
- **M-Pesa prompts are limited** to 3 per phone number per 10 minutes (`STK_LIMIT_PER_PHONE`), so nobody can use the app to spam someone's phone.
- **Phone numbers are masked** on the receipt.
- **Limits:** up to 20 of one product and 50 different products per order. Bigger shops go to a till.

## Prices

There's no Product/Pricing Service yet, so prices come from a JSON file (`PRICES_PATH`, example in `prices.example.json`), in whole shillings by `product_id`. A product with no price can be scanned but not bought; the customer is asked to pay for it at a till.

## Barcode scanning

Android Chrome uses the browser's built-in `BarcodeDetector`. Other browsers (including iPhones) load the ZXing library from jsDelivr the first time the camera starts. The camera only works over HTTPS (or on `localhost`). Typing the number always works.

## Run

```bash
cd apps/scan_and_go
pip install -r requirements-dev.txt
INVENTORY_URL=http://localhost:8010 PAYMENTS_URL=http://localhost:8000 \
uvicorn scan_and_go.app:create_app --factory --port 8030
```

Open http://localhost:8030 on the phone (via HTTPS, e.g. through the hosted Worker or a tunnel, for the camera).

| Setting | Default | Meaning |
|---|---|---|
| `STORE_ID` | `001` | Store the sales are recorded against |
| `STORE_NAME` | `Smart Shopping` | Shown in the header and on the receipt |
| `INVENTORY_URL` | `http://localhost:8010` | Inventory Service |
| `PAYMENTS_URL` | `http://localhost:8000` | Payments Service (its `INVENTORY_URL` must be set so paid sales leave stock) |
| `PRICES_PATH` | `prices.example.json` | Price list |
| `STK_LIMIT_PER_PHONE` | `3` | M-Pesa prompts per phone number per 10 minutes |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The app |
| `GET` | `/api/store` | Store name |
| `GET` | `/api/products/{ean13}` | Look up a scanned product and its price |
| `POST` | `/api/checkout` | `{"phone": "...", "items": [{"product_id", "qty"}]}`: create the sale and send the M-Pesa prompt |
| `GET` | `/api/orders/{order_id}/payments/{payment_id}` | Payment status, and the receipt once paid |
| `POST` | `/api/orders/{order_id}/pay` | Send the M-Pesa prompt again after a cancel or failure |

## Not yet

- **No exit verification yet.** Staff check the pass by eye. A staff scan that checks the order against the Inventory Service could come next.
- **No promotions or weighed items.** Each barcode has one fixed price.
- **No eTIMS receipt yet**, pending the integration route.

## Tests

```bash
pytest
```
