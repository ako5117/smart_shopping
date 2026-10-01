# Running the services: locally or on DigitalOcean

> **HTTPS needs a domain.** On a server, point a domain (or subdomain) at the Droplet before starting.
> Caddy gets the HTTPS certificate automatically, but only for a domain name, not a bare IP address.
> Without HTTPS:
> - the Scan & Go camera won't open, because browsers only allow the camera on HTTPS pages
> - real M-Pesa callbacks won't arrive, because Daraja only calls HTTPS addresses
>
> A local demo on `http://localhost` works fine with the M-Pesa simulator.

`docker-compose.yml` at the repo root runs all four services behind [Caddy](https://caddyserver.com), which handles HTTPS and the staff login:

| Path | Service | Login |
|---|---|---|
| `/` | Redirects to Scan & Go | — |
| `/shop/` | Scan & Go | Public (customers' phones) |
| `/dashboard/` | Store dashboard: Overview, Products, Exit check | Staff |
| `/inventory/...` | Inventory Service API (`/inventory/docs` for the API docs page) | Staff |
| `/pay/...` | Payments Service API (`/pay/docs`) | Staff, except `/pay/payments/mpesa/callback/<secret>`, which Daraja must reach |

Services talk to each other inside Docker's network (`http://inventory:8010`, `http://payments:8000`). Only Caddy is published. The Shelf Service isn't included: it reads the shelf nodes over MQTT and runs in the store.

## Settings that must be set

The stack refuses to start with a clear message if these are missing:

| Setting | Why |
|---|---|
| `STAFF_PASSWORD_HASH` | Staff login. Make it with `docker run --rm caddy:2.10-alpine caddy hash-password --plaintext 'your-password'`, and write each `$` as `$$` in `.env`. |
| `CALLBACK_SECRET` | Secret part of the M-Pesa callback URL. Make it with `openssl rand -hex 24`. |
| `PUBLIC_BASE_URL` | Only for real M-Pesa (`DARAJA_ENV=sandbox` or `production`): the public `https://` address. The Payments Service won't start without it; check `docker compose logs payments`. |

## Demo mode: M-Pesa simulator

`.env.example` starts in demo mode. It has two lines:
- `COMPOSE_PROFILES=demo` starts the M-Pesa simulator (`tools/mpesa_simulator`)
- `DARAJA_ENV=simulator` points the Payments Service at it

A full Scan & Go purchase then works with no Safaricom account and no real money. The phone number decides what happens:

| Number ends in | Result |
|---|---|
| `000` | Customer cancels |
| `111` | Fails (insufficient funds) |
| anything else | Pays after 3 seconds |

The simulator sends its callback through the proxy, like Daraja would. Locally that's `http://proxy` inside Docker. On a server running in demo mode, set `PUBLIC_BASE_URL` to the site's `https://` address instead.

For real M-Pesa, delete `COMPOSE_PROFILES=demo`, set `DARAJA_ENV=sandbox` (or `production`), and fill in the `DARAJA_*` values and `PUBLIC_BASE_URL`.

## Local demo

Needs Docker.

```bash
cp .env.example .env            # then set STAFF_PASSWORD_HASH and CALLBACK_SECRET
docker compose up -d --build
python scripts/seed_demo.py --url http://localhost/inventory --password 'your-password'
```

Then open:

- http://localhost/dashboard/: log in as `staff`
- http://localhost/shop/: Scan & Go. Type a barcode (e.g. `6161000000040`), check out with any number like `0712345678`, and the receipt appears a few seconds later.

`scripts/seed_demo.py` loads the six demo products with prices and stock. Running it twice changes nothing.

## DigitalOcean

1. **Create a Droplet** with the Docker image from the Marketplace (Ubuntu with Docker preinstalled). The Basic 1 GB plan runs the whole stack; 2 GB gives room to grow. Tick **Backups** while creating it (see below).
2. **Point a domain at it.** Add an `A` record, e.g. `shop.awesomtech.co.ke`, to the Droplet's IP, and wait until it resolves.
3. **On the Droplet:**
   ```bash
   git clone https://github.com/ako5117/smart_shopping.git && cd smart_shopping
   cp .env.example .env && nano .env
   ```
   Set these in `.env`:
   - `SITE_ADDRESS=shop.awesomtech.co.ke`
   - `PUBLIC_BASE_URL=https://shop.awesomtech.co.ke`
   - `STAFF_PASSWORD_HASH`
   - `CALLBACK_SECRET`
   - for real M-Pesa, the `DARAJA_*` values (see "Demo mode" above)
4. **Start it:**
   ```bash
   docker compose up -d --build
   ```
   Ports 80 and 443 must be open in the Droplet's firewall. Caddy gets the certificate on first start.
5. **Load products** on the dashboard's Products page, or run `scripts/seed_demo.py` for demo data.

**Updating:**
```bash
git pull && docker compose up -d --build
```

## Your data, and backing it up

Products, stock, sales and payments live in SQLite files inside Docker volumes (`inventory-data`, `payments-data`). They survive restarts, rebuilds and `git pull` updates.

> ⚠️ **`docker compose down -v` deletes all data.** The `-v` removes the volumes. To stop the stack, use `docker compose down` (without `-v`) or `docker compose stop`.

Use one or both of these:

- **DigitalOcean backups:** an image of the whole Droplet, weekly (+20% of the Droplet price) or daily (+30%). Turn it on under the Droplet's **Backups** tab. Restoring rolls back the whole server.
- **Nightly database backups:** `scripts/backup.sh` uses SQLite's online backup, which is safe while the services are running. It saves `inventory-<date>.db` and `payments-<date>.db` to `./backups` and keeps 14 days. Add it to cron on the Droplet:
  ```bash
  crontab -e
  # then add this line:
  30 2 * * * cd /root/smart_shopping && ./scripts/backup.sh >> /var/log/smart-shopping-backup.log 2>&1
  ```
  Copy `./backups` off the server now and then (e.g. to DigitalOcean Spaces or a laptop). A backup on the same disk doesn't survive losing the Droplet.

To restore a database:
1. Stop the stack: `docker compose stop`
2. Copy the backup in, for example `docker compose cp backups/inventory-2026-10-01_0230.db inventory:/data/inventory.db`
3. Start again: `docker compose start`

## Notes

- Production M-Pesa needs the store's own Paybill or Till; see `services/payments/README.md`.
- One shared staff login for now. Per-person accounts come later.
