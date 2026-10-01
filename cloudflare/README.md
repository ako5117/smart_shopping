# Temporary hosting on Cloudflare

Until the company DigitalOcean account exists, the backend runs on **Cloudflare Containers**. Each service runs unchanged from its own Dockerfile, and those same Dockerfiles will run on DigitalOcean later. Nothing in the services depends on Cloudflare.

```
                         https://smart-shopping.<account>.workers.dev
                                           │
                                 Worker (cloudflare/worker.ts)
              staff login for everything except Scan & Go and the M-Pesa callback
      ┌──────────────────┬─────────────────┼──────────────────┐
   /shop/...          /pay/...        /inventory/...      /dashboard/...
   Scan & Go  ──────► Payments ──────► Inventory ◄──────── Dashboard
      └───────────────────────────────────►┘
   Containers call each other at http://payments.internal and http://inventory.internal,
   which the Worker routes inside Cloudflare.
```

| Path | Goes to | Login |
|---|---|---|
| `/` | Links page | — |
| `/shop/` | Scan & Go | Public (customers' phones) |
| `/pay/...` | Payments Service | Staff, except `/pay/payments/mpesa/callback/<secret>` (Daraja must reach it) |
| `/inventory/...` | Inventory Service | Staff |
| `/dashboard/` | Store dashboard: Overview, Products (`/dashboard/products`), Exit check (`/dashboard/exit`) | Staff |

`/inventory/docs` and `/pay/docs` open each service's interactive API docs.

The Shelf Service is not hosted. It talks to the shelf nodes over MQTT and runs in the store.

## Deploys

The repo is connected to Cloudflare **Workers Builds** (Worker name `smart-shopping`). Every push to `main` builds the four images and deploys. Other branches get a preview build check on their pull request.

To deploy by hand instead:

```bash
npm install
npx wrangler login
npx wrangler deploy
```

## One-time setup in the Cloudflare dashboard

1. **Plan.** Containers need the Workers Paid plan (US$5/month, including a usage allowance). The four containers use the smallest instance type (`lite`).
2. **Secrets.** Go to Workers & Pages → `smart-shopping` → Settings → Variables and Secrets, and add these as *Secret*:

   | Name | Value |
   |---|---|
   | `STAFF_PASSWORD` | Password for the staff login (user name `staff`). Until it is set, every staff page returns 503. |
   | `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_PASSKEY` | From the Daraja portal (sandbox for now) |
   | `CALLBACK_SECRET` | A long random string |

3. **Public URL.** Set the variable `PUBLIC_BASE_URL` to the Worker's address, e.g. `https://smart-shopping.<account>.workers.dev`. Daraja callbacks are sent to `<PUBLIC_BASE_URL>/pay/payments/mpesa/callback/<CALLBACK_SECRET>`.

Non-secret settings (`STORE_ID`, `STORE_NAME`, `DARAJA_ENV`, shortcode, etc.) are in `wrangler.jsonc`.

## Limits of this setup

- **Data is not permanent.** Each service keeps SQLite on its container's disk. A deploy, or a container restart by Cloudflare, starts it empty. Inventory and Payments stay up for 24 hours without traffic; the dashboard and Scan & Go for 1 hour. This is fine for demos and sandbox payments, but not for a pilot store. Real data needs the shared database (the next step on DigitalOcean, or Cloudflare D1, or SQLite replicated to R2 with Litestream).
- **One instance per service.** All requests go to the same container, so SQLite stays consistent.
- **The dashboard has no shelf events** because the Shelf Service isn't hosted. It shows stock, sales and differences only.
- **Single shared staff login.** Per-person accounts come later.
- **Products must be added again after a restart** (Inventory starts empty). Use the dashboard's Products page at `/dashboard/products`.

## Moving to DigitalOcean

Build and run the same images, e.g. with Docker Compose on a droplet or as App Platform services:

| Service | Dockerfile | Build context | Port |
|---|---|---|---|
| Payments | `services/payments/Dockerfile` | `services/payments` | 8000 |
| Inventory | `services/inventory/Dockerfile` | `services/inventory` | 8010 |
| Dashboard | `apps/dashboard/Dockerfile` | repo root | 8020 |
| Scan & Go | `apps/scan_and_go/Dockerfile` | `apps/scan_and_go` | 8030 |

Point `INVENTORY_URL` (Payments, Dashboard, Scan & Go) and `PAYMENTS_URL` (Scan & Go) at those services, set the same secrets as environment variables, and put a reverse proxy with a login in front. Then delete `wrangler.jsonc`, `package.json`, `package-lock.json`, `tsconfig.json` and `cloudflare/`, and disconnect Workers Builds.

## Local test with the containers

Needs Docker.

```bash
npm install
printf 'STAFF_PASSWORD=test\nCALLBACK_SECRET=dev\nPUBLIC_BASE_URL=http://localhost:8787\n' > .dev.vars
npx wrangler dev
curl -u staff:test http://localhost:8787/inventory/health
```
