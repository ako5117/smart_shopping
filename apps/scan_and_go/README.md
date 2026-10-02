# Scan & Go

The customer's phone becomes the till. They scan products as they shop, pay with M-Pesa or by card, and show the receipt screen at the exit.

1. **Scan.** The phone camera reads EAN-13 barcodes, or the customer types the number. Each product is looked up and added to the basket with its price.
2. **Pay.** The customer enters their M-Pesa number, or chooses **Card** and their email address. The app creates the sale in the Inventory Service. For M-Pesa, the Payments Service then sends an STK Push to the phone. For a card, the phone goes to the card provider's payment page and comes back to Scan & Go. Card payments suit visitors without M-Pesa, such as travellers at an airport duty-free shop.
3. **Receipt.** When the payment is confirmed, the Payments Service commits the sale, so the items leave stock. The app shows a green **PAID** pass with the order code, a QR code, the M-Pesa receipt number and a live clock, which makes an old screenshot easy to spot at the exit.
4. **Exit.** Staff scan the QR code (or type the code) on the dashboard's **Exit check** page. Once they confirm, the pass on the customer's phone turns grey and reads **CHECKED OUT**, and the same pass can't be used again.

If the customer cancels or the payment fails, they can try again for the same order, by M-Pesa or by card. Nothing leaves stock until a payment succeeds.

## How it stays safe

- **Prices come from the server.** The phone sends only products and quantities. The Inventory Service records each item's price on the sale, and that total is what M-Pesa charges, so changing the page can't lower the price.
- **Order IDs are random** (`SG-` plus 16 hex characters). Looking up an order needs both its ID and its payment ID.
- **M-Pesa prompts are limited** to 3 per phone number per 10 minutes (`STK_LIMIT_PER_PHONE`), so nobody can use the app to spam someone's phone.
- **Phone numbers are masked** on the receipt.
- **Limits:** up to 20 of one product and 50 different products per order. Bigger shops go to a till.

## Prices

Staff set prices on the dashboard's **Products** page; they're stored in the Inventory Service. Scan & Go picks up a change within 30 seconds. A product with no price can be scanned but not bought; the customer is asked to pay for it at a till.

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
| `STK_LIMIT_PER_PHONE` | `3` | M-Pesa prompts per phone number per 10 minutes |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The app |
| `GET` | `/api/store` | Store name |
| `GET` | `/api/products/{ean13}` | Look up a scanned product and its price |
| `POST` | `/api/checkout` | `{"phone": "...", "items": [{"product_id", "qty"}]}`: create the sale and send the M-Pesa prompt |
| `GET` | `/api/orders/{order_id}/payments/{payment_id}` | Payment status, and the receipt once paid |
| `GET` | `/api/orders/{order_id}/qr.svg` | QR code of the order number, for the exit check |
| `POST` | `/api/orders/{order_id}/pay` | Send the M-Pesa prompt again after a cancel or failure |

## Not yet

- **No promotions or weighed items.** Each barcode has one fixed price.
- **No eTIMS receipt yet**, pending the integration route.

## Tests

```bash
pytest
```
