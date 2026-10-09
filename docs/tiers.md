# Tiers: Standard and Smart Shelves

Smart Shopping runs the same system in two tiers. A store starts on **Standard**, which is fully digital and needs no shelf hardware. It can move up to **Smart Shelves** later by fitting the sensors and changing one setting. Its products, stock, sales and logins carry over.

| | Standard | Smart Shelves |
|---|---|---|
| **For** | Most shops and supermarkets; a low-cost start | Stores that want live shelf visibility and less counting by hand |
| **Shelf hardware** | None | Weight sensors and a camera on each shelf |
| **Stock is kept right by** | Barcode scans (restock), sales, and stock counts | All of that, plus the sensors flagging gaps as they happen |
| **Setting** | `STORE_TIER=standard` | `STORE_TIER=smart` |

## What both tiers include

- **Scan & Go:** customers scan barcodes with their own phone, pay by M-Pesa or card, and show a one-time pass at the exit.
- **Online shop:** live stock, substitutes for sold-out items, and click & collect or delivery.
- **Delivery:** the store's own riders, with a rider app and a 4-digit code from the customer as proof of delivery.
- **SMS updates** to online customers: order paid, ready to collect, on its way.
- **Store dashboard:**
  - today's sales and low stock
  - restock by scanning
  - stock counts
  - the exit check
  - packing online orders and assigning riders
  - texts sent
- **Payments:** M-Pesa (STK Push) and cards (Paystack). Payment problems are flagged for a person, for example a customer who paid twice.
- **Retailer sync** (ready for a pilot): stock changes queue for the retailer's own POS or stock system, and any differences are listed for a manager to check. Connecting it needs the retailer's API, so it's set up per retailer.
- **Staff logins** per person, with roles: manager, staff, rider.

## Standard: how stock stays right without sensors

- **Stock only changes when something happens to it:**
  - goods arrive: staff scan them in
  - a sale is paid
  - goods come back as a return
  - a manager records a count
- **Stock counts:** a manager scans a product, types how many are on the shelf and in the store room, and saves. Stock becomes that number. The difference is kept with their name and the time. Many stores count fast-moving products weekly and the rest monthly.
- **The online shop holds one of each product back,** so a miscount rarely means promising something that isn't there.

**What Standard doesn't do:** there's no live view of the shelf. An item that's misplaced, damaged or stolen shows up at the next count rather than as it happens.

## Smart Shelves adds

- **Live shelf activity:** each pick and put-back, per shelf zone.
- **Alerts:**
  - an item put on the wrong shelf
  - a shelf emptier than the records say
  - a weight the camera couldn't confirm
- **Safer online promises:** the online shop doesn't promise items shoppers in the store have just picked up.
- **Faster packing:** packers see which shelf each item is on.

**Hardware, per shelf:**
- a small controller (ESP32)
- one weight sensor and amplifier per zone
- a camera module

The parts list is in [`hardware/bench-prototype.md`](../hardware/bench-prototype.md). Smart Shelves is at the bench-prototype stage: the sensing works with simulated shelves, and the first real test shelf is being built.

## Running costs (both tiers)

| | Cost |
|---|---|
| Server (DigitalOcean, 2 GB) | About US$12 a month for one store |
| M-Pesa and card payments | Safaricom's and Paystack's own transaction fees |
| SMS to customers | Africa's Talking's price per text (about 3 per online order) |
| Shelf hardware (Smart Shelves only) | Per shelf; prices to be confirmed with suppliers |

## Switching tiers

- **Demo:**
  - `./scripts/demo.sh --tier standard` shows a store without sensors.
  - `./scripts/demo.sh --tier smart` brings the simulated smart shelves back.
- **A real store:** run `./scripts/configure.sh`, choose the tier, then `./scripts/deploy.sh`. Nothing is lost either way.
