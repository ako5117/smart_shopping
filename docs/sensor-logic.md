# Sensor Logic — Weight + Optical Hybrid

How a shelf works out *that* something moved, *how many*, and *which product*.

## Principle

Weight answers **how many**. The camera answers **which one**. Neither is trusted alone.

- Two identical items picked together → the weight drop is 2 × unit weight, so quantity 2 is detected correctly. The camera alone would likely miss this.
- Two different products with similar weights on the same zone → the camera decides which one moved. Weight alone cannot.
- A product put back on the wrong shelf → weight goes up by an amount that matches no product assigned to that zone, and the camera identifies the foreign product.

## Physical layout

- Each shelf is split into **zones**. Each zone is a rigid platform resting on its own **load cell**, read through an **HX711** amplifier.
- One **camera** per shelf covers all its zones from above or in front.
- Each zone has a planned product list (`ZONE_ASSIGNMENT`). Ideally one product per zone; mixed zones are supported.

## Where the work happens

| Step | Runs on | Why |
|---|---|---|
| Sampling, filtering, change and settle detection | ESP32 | Needs fast, continuous reads; cheap to do on-device |
| Image capture | ESP32-CAM | On request from the Shelf Service |
| Matching weight to products, image classification, fusion | Shelf Service (site machine) | Needs product data and more compute than an ESP32 has |

## Step 1 — Weight: detect a settled change (ESP32)

1. Read each zone's HX711 at about 10 Hz.
2. Smooth with a moving median over the last 5 readings to remove spikes.
3. **Change detected** when the smoothed weight differs from the last stable weight by more than the zone's threshold:
   `threshold = max(noise_threshold_g, 0.5 × smallest unit weight in the zone)`
4. **Wait to settle**: the weight must stay within ±`settle_band_g` for `settle_time_ms` (hand lifted away, shelf stopped bouncing). A hand resting on the shelf or a lean against it never settles into a new level, so it is ignored.
5. Publish a weight event with the stable weight before and after, then store the new stable weight.
6. **Zero tracking**: when a zone has been stable for a long time with no events, allow very slow drift correction (temperature and creep). Never correct during an event.

Weight event (MQTT, topic `store/{store_id}/shelf/{shelf_id}/zone/{zone_id}/weight`):

```json
{
  "device_id": "shelf-A",
  "zone_id": "A2",
  "event_id": "shelf-A-000153",
  "weight_before_g": 2412.0,
  "weight_after_g": 1608.5,
  "delta_g": -803.5,
  "settled_ms": 850,
  "ts": "2026-09-23T10:14:05+03:00"
}
```

`event_id` is a per-device counter so the backend can drop duplicates if the ESP32 resends after a network drop. The ESP32 buffers events while offline and sends them in order when the connection returns.

## Step 2 — Weight: turn the change into candidates (Shelf Service)

For each product `p` assigned to the zone:

```
n      = |delta_g| / unit_weight_g(p)
qty    = round(n)
error  = |n - qty| × unit_weight_g(p)
fits   = qty ≥ 1 and error ≤ weight_tolerance_g(p) × sqrt(qty)
```

The tolerance grows with quantity because per-item weight variation adds up.

- **Exactly one product fits** → strong candidate; camera confirms.
- **More than one fits** (similar weights) → camera decides.
- **None fits** → possible foreign item, mixed pick of different products, or sensor fault; camera decides whether it is a known product (→ `misplaced`) or unresolved (→ `anomaly`).

## Step 3 — Optical: identify the product (Shelf Service)

1. On a weight event, request a snapshot of the zone from the ESP32-CAM.
2. Compare with the last snapshot taken when the zone was stable (before image).
3. The classifier returns the likely product labels in the changed area with confidence scores.

Phase 1 classifier: a small image model fine-tuned on the pilot products (a handful of SKUs for the bench rig). Mirean leads data collection and training.

## Step 4 — Fuse into a shelf event

| Weight result | Camera result | Shelf event |
|---|---|---|
| Decrease, one product fits | Agrees (or unsure) | `pick`, product, qty from weight |
| Decrease, several fit | Picks one of them | `pick`, product from camera, qty from weight |
| Increase, assigned product fits | Agrees | `return`, product, qty |
| Increase, no assigned product fits | Identifies a known product | `misplaced`, product, alert staff |
| Decrease in zone X and equal increase in zone Y within 10 s | — | `moved`, net zero |
| Any | Disagrees with weight at high confidence | `anomaly`, flag for review |
| No fit | Unsure | `anomaly`, flag for review |

Each event carries a `confidence` value. Low-confidence events are still recorded, but they don't trigger alerts until confirmed.

## What shelf events are used for

Shelf events are **provisional**. They never change committed stock.

- Live "what is on the shelf" view and low-shelf / restock alerts.
- Misplaced-item alerts.
- Data: items picked up and put back, time on shelf, dwell patterns — input for Mirean's analytics.

Committed stock changes only at **checkout** (sale) and **restock** (barcode scan). If an item is picked and bought, the sale commits it. If it is picked and put back, the `return` cancels the `pick` and stock is untouched.

## Tunable parameters (starting values, to be set on the bench)

| Parameter | Start value | Set by |
|---|---|---|
| Sample rate | 10 Hz | Firmware |
| Median window | 5 samples | Firmware |
| `noise_threshold_g` | 3 × measured noise (standard deviation) of an idle zone | Calibration |
| `settle_band_g` | 2 × measured noise | Calibration |
| `settle_time_ms` | 700 ms | Bench test |
| `weight_tolerance_g` per product | Measured spread across 10 units of the product | Product onboarding |
| Moved-item window | 10 s | Bench test |

## Known limits and open questions

- **Products lighter than the noise floor** (e.g. single sweets, very light packets) can't be weighed reliably per item. Options: weigh by pack, or rely on the camera plus restock scans for those zones.
- **Heavy mixed zones** with items of near-identical weight *and* appearance (e.g. flavour variants of the same brand) can defeat both sensors. Planogram rule: keep look-alike variants in separate zones.
- **Lighting and occlusion** affect the camera; the bench test should include low light and a hand left in frame.
- **Entry/exit beams** stay available as a third signal if bench results show a gap.

## Bench test measurements

See [`../hardware/bench-prototype.md`](../hardware/bench-prototype.md). The test must report:

1. Idle noise per zone (g).
2. Quantity accuracy for 1, 2 and 3 items picked together.
3. Correct product rate for two similar-weight products in one zone.
4. False events per hour with no one touching the shelf.
5. Time from hand leaving the shelf to event recorded.
