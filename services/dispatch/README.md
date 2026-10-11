# Dispatch Service

Delivers online orders using the store's own riders. The online shop (`apps/storefront`) creates a delivery when a customer chooses **Deliver to me**. Staff run deliveries from the dashboard's **Online orders** page. Riders work from the rider app (`apps/rider`) on their phone.

## A delivery's life

```
awaiting_payment ──► unassigned ──► assigned ──► picked_up ──► delivered
        │                ▲              │            │   ▲
        ▼                └── released ──┘            ▼   │ staff: try again
    cancelled                                       failed
```

| Status | What happened | Who |
|---|---|---|
| `awaiting_payment` | The order is placed but not paid yet. | Online shop |
| `unassigned` | The Inventory Service shows the order paid. Checked whenever deliveries are read; an order still unpaid after 2 hours, or cancelled, becomes `cancelled`. | Automatic |
| `assigned` | A rider has the job: staff assigned it, or a rider on shift accepted it (first come, first served). Until pickup, staff can move it to another rider and the rider can give it back. | Staff or rider |
| `picked_up` | Staff handed the packed order to the rider. This records the order leaving the store in the Inventory Service (`checked_by: "mary (to rider otieno)"`), like an exit check. | Staff |
| `delivered` | The rider typed the customer's 4-digit delivery code. | Rider |
| `failed` | The rider couldn't deliver and said why. Staff call the customer, then send the rider again. | Rider, then staff |

## Proof of delivery

Each delivery has a random 4-digit code. The customer sees it on their order page and gives it to the rider at the door. The rider can't finish a delivery without it.

- **The code goes to the customer only.** It's returned only with `?include_pin=true`, which only the online shop uses. The rider app never receives it.
- **Wrong guesses are limited.** After 5 wrong codes the delivery locks. Staff call the customer and press **Unlock the code** (`retry`) on the dashboard.

Every step is kept in `delivery_events` (who, when, and the reason for a failure).

## Express or scheduled, priced by distance

The customer chooses:

- **Express:** goes out as soon as it's packed, about an hour. Offered during delivery hours while there's time to arrive before closing.
- **Scheduled:** a 2-hour slot, today or the next two days, starting at least an hour from now.

The fee goes by the distance from the store to the customer's pin (`dispatch/pricing.py`):

    scheduled fee = base + per km x distance        (rounded up to the next KES 10)
    express fee   = scheduled fee x express multiplier (rounded up to the next KES 10)

Distance is the straight line times a road factor (roads wind). With the defaults, 4.4 km costs KES 190 scheduled and KES 290 express. Further than the limit (20 km), the store doesn't deliver: the online shop asks for the customer's location before they shop and says the order would need to be collected from the store.

The fee is worked out here, not by the browser, and the Inventory Service records it on the order, so it's part of what M-Pesa charges. Riders and the dashboard see express jobs first, then scheduled ones by slot.

| Setting | Default | |
|---|---|---|
| `STORE_LAT`, `STORE_LNG` | -1.2864, 36.8172 (central Nairobi) | The store's location |
| `DELIVERY_BASE_KES` | 100 | |
| `DELIVERY_PER_KM_KES` | 20 | |
| `DELIVERY_EXPRESS_MULTIPLIER` | 1.5 | |
| `DELIVERY_MAX_KM` | 20 | Furthest delivery, by road |
| `DELIVERY_HOURS` | 08-20 | Local time (`STORE_TZ`, default Africa/Nairobi) |
| `DELIVERY_SLOT_HOURS` | 2 | |
| `DELIVERY_EXPRESS_MINUTES` | 60 | How long express takes; express stops this long before closing |
| `DELIVERY_ROAD_FACTOR` | 1.3 | Straight line x this ≈ road distance |

Change them in `.env` (or `./scripts/configure.sh`) and redeploy.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/delivery` | How delivery is priced: store location, distance limit, rates, hours |
| `GET` | `/delivery/quote?lat=&lng=` | `{deliverable, distance_km, max_km, reason, express: {available, fee_kes, eta_minutes}, scheduled: {available, fee_kes, slots: [{start, end, label}]}}` |
| `POST` | `/deliveries` | New delivery for an online order (before payment). Repeating it for the same order changes nothing |
| `GET` | `/deliveries?store_id=&status=&rider_id=` | Deliveries: express first, then by time slot (oldest first). `status` is comma-separated; the default is everything still active |
| `GET` | `/deliveries/{sale_id}` | One delivery (`?include_pin=true` for the online shop only) |
| `GET` | `/deliveries/{sale_id}/events` | Its history |
| `POST` | `/deliveries/{sale_id}/assign` | Staff: `{"rider_id", "by"}` |
| `POST` | `/deliveries/{sale_id}/claim` | Rider on shift accepts: `{"rider_id"}` |
| `POST` | `/deliveries/{sale_id}/release` | Back to unassigned before pickup: `{"by", "rider_id"?}` |
| `POST` | `/deliveries/{sale_id}/hand-over` | Staff gave it to the rider: `{"by"}` |
| `POST` | `/deliveries/{sale_id}/delivered` | `{"rider_id", "pin"}`. A wrong code gets `422` with `attempts_left`; once locked, `423` |
| `POST` | `/deliveries/{sale_id}/failed` | `{"rider_id", "reason"}` |
| `POST` | `/deliveries/{sale_id}/retry` | Staff: the rider tries again; also unlocks the code |
| `GET` | `/riders`, `/riders/{id}` | Riders, on shift first, with how many jobs each has |
| `PUT` | `/riders/{id}` | Register a rider or update `phone` / `on_shift` (fields left out are kept) |

Through the proxy, `/dispatch/` is for managers only, like the other service APIs.

## Run and test

```bash
cd services/dispatch
pip install -r requirements-dev.txt
INVENTORY_URL=http://localhost:8010 uvicorn dispatch.api:create_app --factory --port 8050
pytest
```

Storage works like the Inventory Service: `DATABASE_URL` (PostgreSQL, tables in the `DATABASE_SCHEMA` schema, default `dispatch`), or the SQLite file `DISPATCH_DB_PATH` (default `dispatch.db`). `dispatch/sqldb.py` is the shared storage layer and must stay identical to the inventory and payments copies; a test checks this. Set `TEST_DATABASE_URL=postgresql://...` to run the tests against PostgreSQL.

## Not yet

- **Live rider location** on the customer's page.
- **SMS** to the customer when the rider sets off, with the delivery code.
- **Third-party couriers** (for when the store has no rider free). The `claim`/`hand-over`/`delivered` steps are where an adapter would plug in.
- **Refunds for failed deliveries** are done by hand in M-Pesa for now.
