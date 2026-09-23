# Shelf Service

Turns settled weight changes from shelf nodes, plus camera results, into shelf events: `pick`, `return`, `moved`, `misplaced` or `anomaly`. It implements [`docs/sensor-logic.md`](../../docs/sensor-logic.md) and runs at the store, so it keeps working without internet.

Shelf events are provisional. They drive the live shelf view and alerts; committed stock only changes at checkout and restock.

## Try it without hardware

```bash
cd services/shelf
pip install -r requirements-dev.txt
python -m shelf.simulator --config config.example.json
```

The simulator runs nine scenarios with realistic sensor noise — single and multi-item picks, similar-weight products, put-backs, items moved between shelves, wrong-shelf returns, unknown objects, and camera/weight disagreement — and checks each outcome.

## Run it for real

Needs an MQTT broker (e.g. Mosquitto) that the ESP32 shelf nodes publish to.

```bash
python -m shelf.app --config config.example.json --broker localhost \
    --camera A=http://192.168.1.50/capture
```

- Subscribes to `store/+/shelf/+/zone/+/weight` (message format in `docs/sensor-logic.md`, Step 1).
- On each weight event, fetches a snapshot from that shelf's camera if one is configured, classifies it, and fuses the result.
- Prints each shelf event as a JSON line and appends it to `shelf_events.jsonl`.

## Catalogue

`config.example.json` lists products (EAN-13, unit weight, weight tolerance, classifier label) and which products each zone holds. The example items and barcodes are placeholders for the bench prototype.

## Settings to calibrate on the bench

In `FusionSettings` (`shelf/fusion.py`):

| Setting | Default | Meaning |
|---|---|---|
| `sensor_noise_g` | 2.0 | Standard deviation of an idle load cell reading. **Measure this** (bench test step 2). |
| `min_delta_g` | 5.0 | Smaller changes are ignored as noise |
| `camera_min_confidence` | 0.6 | Below this the camera counts as unsure |
| `camera_strong_confidence` | 0.85 | Above this the camera can overrule a single weight match |
| `moved_window_s` | 10 | Pick + matching increase elsewhere within this = moved |
| `alert_min_confidence` | 0.7 | Misplaced items below this are recorded without alerting |

## Camera

The classifier is a stand-in (`UnsureClassifier`) until the trained product model exists. It always reports "unsure", so decisions fall back to weight and ambiguous cases go to review — the safe default. A real model plugs in by implementing `classify(zone_id, jpeg) -> ImageResult`.

## Tests

```bash
pytest
```

## Measured behaviour (simulator, 500 random runs of all nine scenarios)

| Sensor noise | Wrong outcomes |
|---|---|
| 2 g | 3 of 4,500 (0.07%) |
| 4 g | 7 of 4,500 (0.16%) |
| 6 g | 9 of 4,500 (0.2%) |

All remaining misses fail safe: they are flagged for review, or a move is reported as a misplaced item, rather than recorded as a wrong count. These figures assume `sensor_noise_g` is set to the real measured noise; leaving it too low increases errors sharply.
