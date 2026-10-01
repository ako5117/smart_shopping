# Running the services: locally or on DigitalOcean

> **HTTPS needs a domain.** On a server, point a domain (or subdomain) at the Droplet before starting.
> Caddy gets the HTTPS certificate automatically, but only for a domain name, not a bare IP address.
> Without HTTPS:
> - the Scan & Go camera won't open, because browsers only allow the camera on HTTPS pages
> - real M-Pesa callbacks won't arrive, because Daraja only calls HTTPS addresses
>
> A local demo on `http://localhost` works fine with the M-Pesa simulator.

`docker-compose.yml` at the repo root runs all the services behind [Caddy](https://caddyserver.com), which handles HTTPS and the staff login:

| Path | Service | Login |
|---|---|---|
| `/` | Redirects to Scan & Go | — |
| `/shop/` | Scan & Go | Public (customers' phones) |
| `/store/` | Online shop: order online, collect in store | Public |
| `/dashboard/` | Store dashboard: Overview, Products, Exit check | Any staff login |
| `/inventory/...` | Inventory Service API (`/inventory/docs` for the API docs page) | Managers |
| `/pay/...` | Payments Service API (`/pay/docs`) | Managers, except `/pay/payments/mpesa/callback/<secret>`, which Daraja must reach |

Services talk to each other inside Docker's network (`http://inventory:8010`, `http://payments:8000`). Inventory and Payments keep their data in one PostgreSQL database (the `db` service), each in its own schema. Only Caddy is published; the database is reachable only inside Docker. The Shelf Service isn't included: it reads the shelf nodes over MQTT and runs in the store.

## Staff logins

Everyone has their own login. Logins are managed with `scripts/staff.sh` and kept in `deploy/staff/` (git-ignored):

```bash
./scripts/staff.sh add adrian --manager   # asks for a password (at least 8 characters)
./scripts/staff.sh add mary               # ordinary staff
./scripts/staff.sh list
./scripts/staff.sh password mary          # new password
./scripts/staff.sh role mary manager      # or: role mary staff
./scripts/staff.sh remove mary
```

Changes take effect immediately, with no restart. **Add at least one manager before the first start**: the proxy won't start with no logins.

| | Staff | Manager |
|---|---|---|
| Overview, restock, exit check | ✓ | ✓ |
| Add products, change prices | | ✓ |
| Record a stock count (correct a difference) | | ✓ |
| Raw Inventory and Payments APIs (`/inventory/`, `/pay/`) | | ✓ |

The dashboard records who did what:
- each restock and stock correction
- each price change, with the old and new price
- each exit check

The signed-in name comes from the login, so nobody types a name, and the proxy overwrites anything a browser sends in its place.

The browser remembers a login until it's closed. On a shared till computer, close the browser at the end of a shift.

## Settings that must be set

The stack refuses to start with a clear message if these are missing:

| Setting | Why |
|---|---|
| `CALLBACK_SECRET` | Secret part of the M-Pesa callback URL. Make it with `openssl rand -hex 24`. |
| `DB_PASSWORD` | Password for the shared database. Make it the same way. Set it before the first start: the database keeps the password it was created with, so changing it later in `.env` alone locks the services out. |
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
./scripts/demo.sh
```

That sets up `.env`, creates a `manager` and a `staff` login, starts everything and loads demo products. See [`docs/demo-walkthrough.md`](../docs/demo-walkthrough.md).

To do it by hand instead:

```bash
cp .env.example .env            # then set CALLBACK_SECRET and DB_PASSWORD
./scripts/staff.sh add yourname --manager
docker compose up -d --build
python scripts/seed_demo.py --url http://localhost/inventory --user yourname --password 'your-password'
```

Then open:

- http://localhost/dashboard/: log in with your name
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
   - `CALLBACK_SECRET` and `DB_PASSWORD`
   - for real M-Pesa, the `DARAJA_*` values (see "Demo mode" above)
   Then add the logins: `./scripts/staff.sh add <your-name> --manager`, plus one per staff member.
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

Products, stock, sales, payments and who-did-what all live in one PostgreSQL database, kept in the Docker volume `db-data`. It survives restarts, rebuilds and `git pull` updates.

> ⚠️ **`docker compose down -v` deletes all data.** The `-v` removes the volumes. To stop the stack, use `docker compose down` (without `-v`) or `docker compose stop`.

Use one or both of these:

- **DigitalOcean backups:** an image of the whole Droplet, weekly (+20% of the Droplet price) or daily (+30%). Turn it on under the Droplet's **Backups** tab. Restoring rolls back the whole server.
- **Nightly database backups:** `scripts/backup.sh` runs `pg_dump`, which is safe while the services are running. It saves `smartshopping-<date>.dump` to `./backups` and keeps 14 days. Add it to cron on the Droplet:
  ```bash
  crontab -e
  # then add this line:
  30 2 * * * cd /root/smart_shopping && ./scripts/backup.sh >> /var/log/smart-shopping-backup.log 2>&1
  ```
  Copy `./backups` off the server now and then (e.g. to DigitalOcean Spaces or a laptop). A backup on the same disk doesn't survive losing the Droplet.

To restore a backup (this replaces the current data):
```bash
docker compose stop inventory payments
docker compose exec -T db pg_restore -U smartshopping -d smartshopping --clean --if-exists < backups/smartshopping-2026-10-01_0230.dump
docker compose start inventory payments
```

**Managed database (optional, later):** with several stores, DigitalOcean Managed PostgreSQL (from about $15 a month) takes over backups, updates and failover. Point `DATABASE_URL` for `inventory` and `payments` at it (add `?sslmode=require`) and drop the `db` service.

## Notes

- Production M-Pesa needs the store's own Paybill or Till; see `services/payments/README.md`.
- Staff logins use the browser's built-in sign-in box (HTTP Basic over HTTPS). There's no "log out" button: closing the browser signs out.
