# Bench Prototype — Hybrid Shelf (Weight + Camera)

A two-zone test shelf to prove the sensing logic in [`../docs/sensor-logic.md`](../docs/sensor-logic.md) before any store pilot.

> Replaces the earlier IR break-beam parts list (entry/exit sensing), which was superseded at the 22 Sep 2026 meeting. Hold any break-beam purchases.

## Parts

| # | Item | Qty | Purpose | Price (KES, to confirm locally) |
|---|---|---|---|---|
| 1 | ESP32 DevKitC (WROOM-32) | 1 | Reads load cells, publishes events over WiFi | |
| 2 | Straight-bar load cell, 5 kg | 2 | One per zone | |
| 3 | HX711 load cell amplifier module | 2 | One per load cell | |
| 4 | ESP32-CAM (AI-Thinker, OV2640) | 1 | Zone snapshots | |
| 5 | ESP32-CAM-MB USB programmer board | 1 | Programming the ESP32-CAM | |
| 6 | Rigid platform, ~20 × 20 cm (acrylic or MDF) | 2 | Zone surface on top of each load cell | |
| 7 | Rigid base board | 1 | Mounts both load cells | |
| 8 | M4/M5 screws and spacers | 1 set | Load cell mounting (cantilever) | |
| 9 | 5 V / 2 A power supply | 1 | Powers both boards | |
| 10 | Breadboard + jumper wires | 1 set | Wiring | |
| 11 | Known test items (e.g. 500 g and 1 kg packs, two similar-weight products) | — | Calibration and tests | |

Notes:
- 5 kg bar cells suit typical grocery items; for heavy items (flour, rice bags) use 20 kg cells.
- Load cells must be mounted cantilever-style (one end fixed to the base, the other supporting the platform) with a gap underneath, or readings will be wrong.
- Buy one spare HX711; they are cheap and easily damaged.

## Wiring overview

| From | To (ESP32) |
|---|---|
| HX711 #1 DT / SCK | GPIO 16 / GPIO 4 |
| HX711 #2 DT / SCK | GPIO 17 / GPIO 5 |
| HX711 VCC / GND | 3.3 V / GND |
| Load cell red / black / white / green | HX711 E+ / E− / A− / A+ (colours vary by supplier — check the datasheet) |

The ESP32-CAM is a separate board on the same 5 V supply and WiFi network. It serves a snapshot over HTTP when the Shelf Service requests one.

## Test plan

1. **Calibrate** each zone with a known weight; record the calibration factor.
2. **Idle noise**: log 30 minutes with the shelf untouched. Record noise (standard deviation) and false events.
3. **Single picks**: pick and replace one item 20 times per product. Record detected quantity and product.
4. **Multi picks**: pick 2 and 3 identical items together, 10 times each.
5. **Similar weights**: two different products of near-equal weight in one zone; pick each 10 times. Record whether the camera picks the right one.
6. **Misplaced**: put a product from zone 1 into zone 2.
7. **Disturbances**: lean on the shelf, rest a hand for 5 seconds, tap the platform.
8. **Offline**: disconnect WiFi, make 5 picks, reconnect; confirm all 5 arrive in order with no duplicates.
9. **Lighting**: repeat step 5 in low light.

Record results in `hardware/results/` as dated files.
