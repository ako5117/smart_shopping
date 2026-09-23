# Shelf Node Firmware (ESP32)

Reads one load cell per shelf zone, detects when the weight has changed and settled, and publishes the change over MQTT to the Shelf Service. Implements Step 1 of [`docs/sensor-logic.md`](../../../docs/sensor-logic.md).

## Setup

1. Install the Arduino IDE (or `arduino-cli`) with the **esp32** board package.
2. Install libraries: **HX711** by Rob Tillaart and **PubSubClient** by Nick O'Leary. (Bogdan Necula's "HX711 Arduino Library" also works.)
3. Copy `config.example.h` to `config.h` and set WiFi, broker address, shelf identity and zone pins. `config.h` is git-ignored because it holds the WiFi password.
4. Board: *ESP32 Dev Module*. Upload `shelf_node.ino`.

Wiring is in [`../../bench-prototype.md`](../../bench-prototype.md).

## Calibrating each zone (Serial Monitor, 115200 baud)

| Command | When |
|---|---|
| `tare A1` | Zone empty — sets zero |
| `cal A1 1000` | With a known 1000 g weight on the zone |
| `noise A1` | Zone untouched for ~10 s — prints noise and suggested `threshold_g` / `settle_band_g` |
| `show` | Current readings, calibration factors, queued events |

Calibration is saved on the board and survives restarts. Put the measured noise into `config.h` and into the Shelf Service's `sensor_noise_g` setting.

## Behaviour

- Samples each zone at 10 Hz and smooths with a 5-reading median.
- An event is sent only after the weight holds steady for 700 ms, so leaning on the shelf or resting a hand does not produce events.
- Event ids combine a boot counter and a sequence number, so they stay unique across restarts and the Shelf Service can drop duplicates.
- While WiFi or the broker is down, up to 50 events are queued and sent in order on reconnect. If the queue fills, the oldest event is dropped and a warning is printed.
- Timestamps come from NTP (East Africa Time). Until the clock is set, events carry an empty timestamp and the Shelf Service uses its own time.

Known limit: a hand pressed completely still on a zone for longer than the settle time produces two events (weight up, then back down). The Shelf Service flags both as anomalies for review rather than miscounting. Normal hand tremor keeps the reading from settling, so this is rare.

## Tests (no hardware needed)

```bash
cd test && sh run_tests.sh
```

Runs the real firmware logic on a PC with simulated load cells, clock and MQTT, covering idle noise, slow drift, a pick with a bouncing shelf, a resting hand, a two-item pick, and offline queueing. Needs `g++`.

Compiled with arduino-cli for *ESP32 Dev Module* (esp32 core 3.3): 71% of program storage, 21% of RAM.
