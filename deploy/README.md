# Running the services: locally or on DigitalOcean

> **HTTPS needs a domain.** On a server, point a domain (or subdomain) at the Droplet before starting.
> Caddy gets the HTTPS certificate automatically, but only for a domain name, not a bare IP address.
> Without HTTPS:
> - the Scan & Go camera won't open, because browsers only allow the camera on HTTPS pages
> - real M-Pesa callbacks won't arrive, because Daraja only calls HTTPS addresses
>
> A local demo on `http://localhost` works fine with the simulators.

`docker-compose.yml` at the repo root runs all the services behind [Caddy](https://caddyserver.com), which handles HTTPS and the staff login:

| Path | Service | Login |
|---|---|---|
| `/` | Redirects to Scan & Go | — |
| `/shop/` | Scan & Go | Public (customers' phones) |
| `/store/` | Online shop: order online, collect in store or have it delivered | Public |
| `/rider/` | Rider app: deliveries on the rider's phone | Riders (and managers) |
| `/dashboard/` | Store dashboard: Overview, Products, Exit check | Any staff login |
| `/inventory/...` | Inventory Service API (`/inventory/docs` for the API docs page) | Managers |
| `/dispatch/...` | Dispatch Service API (`/dispatch/docs`) | Managers |
| `/pay/...` | Payments Service API (`/pay/docs`) | Managers, except `/pay/payments/mpesa/callback/<secret>` and `/pay/payments/card/webhook` (signed), which Daraja and the card provider must reach |
| `/card-sim/` | Card simulator's test payment page | Demo mode only |

Services talk to each other inside Docker's network (`http://inventory:8010`, `http://payments:8000`). Inventory and Payments keep their data in one PostgreSQL database (the `db` service), each in its own schema. Only Caddy is published; the database is reachable only inside Docker. The Shelf Service isn't included: it reads the shelf nodes over MQTT and runs in the store.

## Staff logins

Everyone has their own login. Logins are managed with `scripts/staff.sh` and kept in `deploy/staff/` (git-ignored):

```bash
./scripts/staff.sh add adrian --manager   # asks for a password (at least 8 characters)
./scripts/staff.sh add mary               # ordinary staff
./scripts/staff.sh add otieno --rider     # a rider: the rider app only
./scripts/staff.sh list
./scripts/staff.sh password mary          # new password
./scripts/staff.sh role mary manager      # or: role mary staff / rider
./scripts/staff.sh remove mary
```

Changes take effect immediately, with no restart. **Add at least one manager before the first start**: the proxy won't start with no logins.

| | Rider | Staff | Manager |
|---|---|---|---|
| Rider app (`/rider/`): accept and deliver orders | ✓ | | ✓ |
| Dashboard: overview, restock, exit check, pack and dispatch online orders | | ✓ | ✓ |
| Add products, change prices | | | ✓ |
| Record a stock count (correct a difference) | | | ✓ |
| Raw Inventory, Dispatch and Payments APIs (`/inventory/`, `/dispatch/`, `/pay/`) | | | ✓ |

The dashboard records who did what:
- each restock and stock correction
- each price change, with the old and new price
- each exit check
- each packed online order, rider assignment and handover to a rider

The signed-in name comes from the login, so nobody types a name, and the proxy overwrites anything a browser sends in its place.

The browser remembers a login until it's closed. On a shared till computer, close the browser at the end of a shift.

## Settings that must be set

The stack refuses to start with a clear message if these are missing:

| Setting | Why |
|---|---|
| `CALLBACK_SECRET` | Secret part of the M-Pesa callback URL. Make it with `openssl rand -hex 24`. |
| `DB_PASSWORD` | Password for the shared database. Make it the same way. Set it before the first start: the database keeps the password it was created with, so changing it later in `.env` alone locks the services out. |
| `PUBLIC_BASE_URL` | Only for real M-Pesa (`DARAJA_ENV=sandbox` or `production`): the public `https://` address. The Payments Service won't start without it; check `docker compose logs payments`. |
| `PAYSTACK_SECRET_KEY` | Only for real cards (`CARD_PROVIDER=paystack`). See `services/payments/README.md`, "Setting up Paystack". |
| `AT_USERNAME`, `AT_API_KEY` | Only for real texts (`SMS_PROVIDER=africastalking`). See `services/notify/README.md`, "Setting up Africa's Talking". |

## Demo mode: M-Pesa and card simulators

`.env.example` starts in demo mode:
- `COMPOSE_PROFILES=demo` starts the M-Pesa simulator (`tools/mpesa_simulator`) and the card simulator (`tools/card_simulator`)
- `DARAJA_ENV=simulator` points the Payments Service at the M-Pesa simulator
- `CARD_PROVIDER=simulator` points it at the card simulator
- `SMS_PROVIDER=simulator` records the texts customers would get, without sending them. They show under **Texts to customers** on the dashboard's Online orders page

A full purchase then works with no Safaricom or Paystack account and no real money. For M-Pesa, the phone number decides what happens:

| Number ends in | Result |
|---|---|
| `000` | Customer cancels |
| `111` | Fails (insufficient funds) |
| anything else | Pays after 3 seconds |

The simulator sends its callback through the proxy, like Daraja would. Locally that's `http://proxy` inside Docker. On a server running in demo mode, set `PUBLIC_BASE_URL` to the site's `https://` address instead.

For cards, the simulator's test payment page (at `/card-sim/`) takes `4084 0840 8408 4081` to pay and `4000 0000 0000 0002` to be declined. It sends its signed webhook through the proxy, like Paystack would.

For real payments, delete `COMPOSE_PROFILES=demo` and:
- set `DARAJA_ENV=sandbox` (or `production`), and fill in the `DARAJA_*` values and `PUBLIC_BASE_URL`
- set `CARD_PROVIDER=paystack` and `PAYSTACK_SECRET_KEY`, or leave `CARD_PROVIDER` empty for M-Pesa only
- set `SMS_PROVIDER=africastalking` and the `AT_*` values, or leave `SMS_PROVIDER` empty for no texts

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

Four scripts do the work on the server:

| Script | What it does |
|---|---|
| `scripts/server-setup.sh` | Once, on a fresh Droplet. Installs Docker, sets the firewall to SSH, HTTP and HTTPS only, adds 2 GB of swap, turns on automatic security updates, sets Nairobi time, and schedules the nightly backup. |
| `scripts/configure.sh` | Asks a few questions and writes `.env`: domain, live store or partner demo, store name, M-Pesa, cards, delivery areas, off-site backups. It generates the passwords. Run it again to change answers; it keeps the database password. |
| `scripts/preflight.sh` | Checks everything before a deploy: settings, logins, DNS, disk, memory and backups. It refuses on anything that would break the store or take real money wrongly. |
| `scripts/deploy.sh` | Preflight, then a database backup, then pulls the latest code, builds, starts, waits until every service is healthy, and checks the public pages. It prints the command to go back to the previous version. |

### Which Droplet

The whole stack uses about **400 MB of memory** when running (measured: Postgres about 65 MB, each service 40–50 MB, the proxy about 12 MB). Building the images needs more, and that's what swap is for.

| Plan | Price | Fits |
|---|---|---|
| Basic, 1 GB / 1 CPU | $6 a month | A pilot store. Builds are slow, and lean on swap. |
| **Basic, 2 GB / 1 CPU** | **$12 a month** | **Recommended:** room for builds, growth and the demo simulators. |
| + Droplet backups | +20% ($2.40 on 2 GB) | Weekly image of the whole server. |
| + Spaces | $5 a month (250 GB) | Off-site copies of the nightly database backups. |

Prices are DigitalOcean's list prices; check its pricing page when ordering. Choose a region near Kenya with low latency: **Frankfurt (fra1)** or **London (lon1)**. Choose **Ubuntu 24.04**.

### First deploy

1. **Create the Droplet** (Ubuntu 24.04, 2 GB). Add your SSH key and tick **Backups**. In **Networking → Firewalls**, you can also add a DigitalOcean cloud firewall allowing only 22, 80 and 443. The server's own firewall does the same, so this is a second layer.
2. **Point the domain at it.** Add an `A` record, e.g. `shop.awesomtech.co.ke`, pointing at the Droplet's IP.
3. **On the Droplet** (`ssh root@<ip>`):
   ```bash
   git clone https://github.com/ako5117/smart_shopping.git /opt/smart_shopping
   cd /opt/smart_shopping
   ./scripts/server-setup.sh
   ./scripts/configure.sh                     # live store, or "demo" for a partner demo
   ./scripts/staff.sh add <your-name> --manager
   ./scripts/deploy.sh
   ```
   `deploy.sh` stops at the first check if something's missing, such as the domain not resolving yet or no manager login, and says what to do. Caddy gets the HTTPS certificate on the first start; that needs the domain to point at the Droplet and ports 80 and 443 open.
4. **Add the people:** a login per member of staff (`./scripts/staff.sh add mary`) and per rider (`./scripts/staff.sh add otieno --rider`). Remove anyone who leaves: `./scripts/staff.sh remove mary`.
5. **Load products** on the dashboard's Products page (prices, categories), and restock to set the starting stock.

### Updating, and going back

```bash
cd /opt/smart_shopping
./scripts/deploy.sh                 # backs up the database first, then updates to the latest code
./scripts/deploy.sh --ref <commit>  # back to an earlier version (the previous one is printed after each deploy)
```

The database is never rolled back by a deploy. If an update went wrong with the data too, restore the backup that `deploy.sh` took just before (see below).

### Going live checklist

- **M-Pesa:** do the **Go Live** process on the Daraja portal for the store's own Paybill or Till.
  - In `./scripts/configure.sh`, choose `production` and enter the production consumer key and secret, the shortcode and the passkey. For a Till, choose `CustomerBuyGoodsOnline` and enter the till number.
  - Safaricom sends results to `https://<domain>/pay/payments/mpesa/callback/<secret>`, which is already set up.
  - The Daraja **security credential** (the encrypted initiator password) isn't needed: it's only for refunds, payouts and balance queries, which aren't built yet. Don't put it anywhere until they are.
- **Cards** (optional): use a Paystack live key (`sk_live_...`), and set the webhook URL on the Paystack dashboard to `https://<domain>/pay/payments/card/webhook`. Run one test-key payment end to end first.
- **Texts** (optional): an Africa's Talking live app with SMS credit, its username and API key in `./scripts/configure.sh`, and a sender ID once approved. Place a test order with your own number and check the texts arrive.
- **Delivery areas** and fees for the store (`./scripts/configure.sh`).
- **Logins:** everyone has their own. Remove the demo logins (`manager`, `staff`, `rider`) if the server was a demo first.
- **Backups:** `./scripts/preflight.sh` shows whether the nightly backup is scheduled and whether copies go off-site.
- **A real purchase:** buy something cheap with Scan & Go and the online shop, check it on the dashboard, then refund it by hand.

## Your data, and backing it up

Products, stock, sales, payments, deliveries, texts and who-did-what all live in one PostgreSQL database, kept in the Docker volume `db-data`. It survives restarts, rebuilds and updates.

> ⚠️ **`docker compose down -v` deletes all data.** The `-v` removes the volumes. To stop the stack, use `docker compose down` (without `-v`) or `docker compose stop`.

There are three layers:

- **Nightly database backup:** `scripts/backup.sh`, scheduled for 02:30 by `server-setup.sh`. It runs `pg_dump`, which is safe while the services are running. It keeps 14 days in `/opt/smart_shopping/backups`, and logs to `/var/log/smart-shopping-backup.log`. `deploy.sh` also takes one before every update.
- **Off-site copies:** with a Spaces bucket set in `configure.sh` (create the bucket and an access key under **Spaces Object Storage** on DigitalOcean), each backup is also copied to `smart-shopping/` in the bucket. A backup on the Droplet's own disk doesn't survive losing the Droplet. Set a lifecycle rule on the bucket to delete copies after 90 days.
- **Droplet backups:** a weekly image of the whole server, +20% of the Droplet price. Restoring rolls back everything, code and data.

To restore a database backup (this replaces the current data):
```bash
cd /opt/smart_shopping
docker compose stop inventory payments dispatch notify
docker compose exec -T db pg_restore -U smartshopping -d smartshopping --clean --if-exists < backups/smartshopping-2026-10-01_0230.dump
docker compose start inventory payments dispatch notify
```
For a copy from Spaces, download it first, e.g. from the Spaces page in the DigitalOcean control panel, into `backups/`.

**Managed database (optional, later):** with several stores, DigitalOcean Managed PostgreSQL (from about $15 a month) takes over backups, updates and failover. Point `DATABASE_URL` for `inventory`, `payments`, `dispatch` and `notify` at it (add `?sslmode=require`) and drop the `db` service.

## Notes

- Production M-Pesa needs the store's own Paybill or Till; see `services/payments/README.md`.
- Staff logins use the browser's built-in sign-in box (HTTP Basic over HTTPS). There's no "log out" button: closing the browser signs out.
