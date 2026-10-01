# Demo walkthrough

A 10–15 minute demo of Phase 1 for a partner or pilot store. It runs on one laptop. There's no shelf hardware, no Safaricom account and no real money:
- **M-Pesa** is a simulator that behaves like Daraja.
- **Shelf sensors** are simulated. The real weight-and-camera logic runs on scripted shelf activity.

Everything else is the real software.

## Before the meeting

1. Install [Docker Desktop](https://docs.docker.com/get-docker/) and clone the repo.
2. Run `./scripts/demo.sh` (on Windows, from Git Bash or WSL). The first run builds everything (a few minutes) and prints:
   - the dashboard address, and two logins: `manager` (can change prices and correct counts) and `staff` (can restock and do exit checks)
   - the Scan & Go address, including one for a phone on the same Wi-Fi
3. Open two windows side by side:
   - **Staff:** http://localhost/dashboard/ (log in as `manager`)
   - **Customer:** http://localhost/shop/. Use a phone if you have one: open the "same Wi-Fi" address the script printed. Otherwise use a narrow browser window.
4. Starting from an earlier demo? `./scripts/demo.sh --reset` gives clean data.

> The customer's camera only works over HTTPS, so on a laptop demo you **type the barcode** instead of scanning. On the hosted version (with a domain), the camera scans for real.

| Barcode to type | Product | Price |
|---|---|---|
| `6161000000040` | Milk 500 ml | KES 65 |
| `6161000000026` | Sugar 1 kg | KES 180 |
| `6161000000064` | Bread 400 g | KES 70 |
| `6161000000033` | Rice 1 kg | KES 220 |

| Phone number to pay with | What the M-Pesa simulator does |
|---|---|
| `0712 345 678` (anything not below) | Pays after 3 seconds |
| ends in `000`, e.g. `0712 345 000` | Customer cancels on the phone |
| ends in `111` | Fails: insufficient funds |

## The demo

### 1. The store at a glance (Staff: Overview), 2 min

- **Today:** sales and items sold, starting at zero.
- **Shelves:** each shelf zone and the committed stock of what belongs there.
- **Shelf activity:** live from the shelf sensors. Load cells weigh each zone, and a camera confirms which product moved.
- **Key point: shelf events never change stock on their own.** Someone picking up an item and putting it back costs nothing. Stock changes only when an item is paid for, restocked, or counted.
- **Needs attention:** something the sensors couldn't settle, e.g. an item left on the wrong shelf, or a weight that matches more than one product. Staff check it instead of the system guessing.

### 2. Products and prices (Staff: Products), 2 min

> Signed in as `manager`. Everything below is recorded under the signed-in name.

- **Restock:** type `6161000000040` (milk), quantity `12`, then **Add to stock**. In a store, a handheld scanner types the barcode.
- **Change a price:** click **Milk 500 ml** in the list, change the price to `70`, then **Save product**. Scan & Go charges the new price within 30 seconds.
- **Who's allowed:** in a private window, sign in as `staff`. The price editor is gone, and staff can restock but not change prices. Each person has their own login.
- **The store's own system:** every stock change is queued to sync with the store's existing till system (see **Retailer sync** at the top of the Overview). We connect to the store's POS rather than replace it.

### 3. The customer shops (Customer: Scan & Go), 3 min

- **Scan:** type `6161000000040` twice and `6161000000026` once. Show the running total and the + / − buttons.
- **Pay:** tap **Checkout**, enter `0712 345 678`, then **Pay**.
  - Narrate: "an M-Pesa prompt now appears on the customer's phone and they enter their PIN."
  - The simulator approves it after 3 seconds.
- **The PAID pass:** order code, QR code, M-Pesa reference, and a live clock. The clock makes an old screenshot easy to spot at the door.

**Optional, a failed payment:** start a new basket, add bread, and pay with `0712 345 000`.
- The customer "cancels" and the app says nothing was charged.
- Pay again with `0712 345 678` and it goes through. It's the same order, so no duplicate is created.

### 4. Leaving the store (Staff: Exit check), 2 min

- **Check the pass:** type the 8-character code from the customer's pass (in a store, a handheld scanner reads the QR).
- **PAID ✓** shows exactly what was bought, so staff can compare it with the bag.
- **Confirm:** press **Confirm**. It's recorded under whoever is signed in. The customer's pass turns grey and says **CHECKED OUT**.
- **Try to reuse it:** check the same code again and it shows **PASS ALREADY USED**, with the time and the staff name.

### 5. Back to the store view (Staff: Overview), 2 min

- **Today:** now shows the paid sales and items sold.
- **Stock:** dropped by exactly what was paid for, and nothing else.
- **Recent sales:** shows the M-Pesa references.
- **The shelf view keeps moving.** A misplaced item turns up under **Needs attention**: the shelf system noticed something left on the wrong shelf.

### 6. Questions people usually ask

- **"What if the internet drops?"** The shelf system runs in the store. Changes queue up and sync when the connection is back.
- **"What if our counts and yours differ?"** The **Differences** panel shows each one. Staff count the shelf and the system corrects itself, with the reason recorded.
- **"Do we have to replace our till?"** No. We connect to the store's existing POS.
- **"What's real here?"** All the software is. The two simulated pieces are M-Pesa (until the store's Paybill or Till is connected) and the shelf sensors (until the bench prototype is wired in).

## After the demo

- `./scripts/demo.sh --stop` stops everything and keeps the data.
- `./scripts/demo.sh --reset` wipes the demo data and starts fresh.
