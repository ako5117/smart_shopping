# Rider app

The store's riders use this on their phone, at `/rider/`. Each rider signs in with their own login, made with `./scripts/staff.sh add <name> --rider`. A rider login only opens the rider app, not the dashboard.

1. **Shift.** The rider saves the phone number customers will see, then taps **Start shift**. Only riders on shift see deliveries waiting for a rider.
2. **Accept.** A waiting delivery shows only its area, order code and fee. The customer's name, phone and address appear once the rider accepts it, or once staff assign it on the dashboard. First come, first served: if two riders tap at once, one gets it and the other is told.
3. **Collect.** The rider goes to the counter with the order code. Staff hand the parcel over on the dashboard's **Online orders** page, and the job changes to **Delivering**.
4. **Deliver.** **Map** opens Google Maps with directions to the customer's pin, or a search for their address if they didn't share a location. **Call** rings the customer. At the door, the rider types the customer's 4-digit code and taps **Delivered**. The customer's page changes to **Delivered**.
5. **Problem.** If the delivery can't be made (no answer, wrong address), the rider taps **Problem** and says why. Staff see it on the dashboard, call the customer, and send the rider again.

A rider can give a job back with **Give back** until they've picked it up. After 5 wrong codes the delivery locks until staff unlock it.

## Run

```bash
cd apps/rider
pip install -r requirements-dev.txt
DISPATCH_URL=http://localhost:8050 INVENTORY_URL=http://localhost:8010 DEV_RIDER=otieno \
uvicorn rider.app:create_app --factory --port 8060
```

`DEV_RIDER` stands in for the login when running without the proxy. Behind the proxy, the rider's name comes from their login, in `X-Staff-User`.

| Setting | Default | Meaning |
|---|---|---|
| `STORE_ID`, `STORE_NAME` | `001`, `Smart Shopping` | Which store's deliveries, and the name in the header |
| `DISPATCH_URL` | `http://localhost:8050` | Dispatch Service |
| `INVENTORY_URL` | `http://localhost:8010` | Inventory Service (item counts) |
| `STORE_CITY` | `Nairobi` | Added to map searches for addresses without a pin |

## Tests

```bash
pytest
```
