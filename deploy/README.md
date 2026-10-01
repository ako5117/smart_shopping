# Running the services: locally or on DigitalOcean

`docker-compose.yml` at the repo root runs all four services behind [Caddy](https://caddyserver.com), which handles HTTPS and the staff login:

| Path | Service | Login |
|---|---|---|
| `/` | Redirects to Scan & Go | — |
| `/shop/` | Scan & Go | Public (customers' phones) |
| `/dashboard/` | Store dashboard: Overview, Products, Exit check | Staff |
| `/inventory/...` | Inventory Service API (`/inventory/docs` for the API docs page) | Staff |
| `/pay/...` | Payments Service API (`/pay/docs`) | Staff, except `/pay/payments/mpesa/callback/<secret>`, which Daraja must reach |

Services talk to each other inside Docker's network (`http://inventory:8010`, `http://payments:8000`). Only Caddy is published. Each service keeps its SQLite file in a Docker volume, so data survives restarts and redeploys.

The Shelf Service isn't included: it reads the shelf nodes over MQTT and runs in the store.

## Local demo

Needs Docker.

```bash
cp .env.example .env
docker run --rm caddy:2.10-alpine caddy hash-password --plaintext 'pick-a-password'
#   paste the output into STAFF_PASSWORD_HASH in .env, writing each $ as $$
docker compose up -d --build
python scripts/seed_demo.py --url http://localhost/inventory --password 'pick-a-password'
```

Then open:

- http://localhost/dashboard/: log in as `staff`
- http://localhost/shop/: Scan & Go. Typing barcodes works over plain http. The phone camera needs HTTPS, so test it on the server set up below.

`scripts/seed_demo.py` loads the six demo products with prices and stock. Running it twice changes nothing.

## DigitalOcean

1. **Create a Droplet** with the Docker image from the Marketplace (Ubuntu with Docker preinstalled). The smallest Basic size (1 GB RAM) runs all four services.
2. **Point a domain at it.** Add an `A` record, e.g. `shop.awesomtech.co.ke`, to the Droplet's IP.
3. **On the Droplet:**
   ```bash
   git clone https://github.com/ako5117/smart_shopping.git && cd smart_shopping
   cp .env.example .env && nano .env
   ```
   Set these in `.env`:
   - `SITE_ADDRESS=shop.awesomtech.co.ke`
   - `PUBLIC_BASE_URL=https://shop.awesomtech.co.ke`
   - `STAFF_PASSWORD_HASH`
   - the `DARAJA_*` values
   - a long random `CALLBACK_SECRET`
4. **Start it:**
   ```bash
   docker compose up -d --build
   ```
   Caddy gets the HTTPS certificate automatically on first start. Ports 80 and 443 must be open in the Droplet's firewall.
5. **Load products.** Use the dashboard's Products page, or run `scripts/seed_demo.py` for demo data.

**Updating:**
```bash
git pull && docker compose up -d --build
```

**Backups:** the data lives in the `inventory-data` and `payments-data` volumes. Turn on Droplet backups, or copy the `.db` files out with `docker compose cp inventory:/data/inventory.db ./backup/` until the shared database replaces SQLite.

## Notes

- Daraja callbacks go to `<PUBLIC_BASE_URL>/pay/payments/mpesa/callback/<CALLBACK_SECRET>`. Production M-Pesa needs the store's own Paybill or Till; see `services/payments/README.md`.
- One shared staff login for now. Per-person accounts come later.
