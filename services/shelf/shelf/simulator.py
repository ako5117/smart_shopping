"""Simulated shelf activity, for developing and demonstrating the Shelf Service without hardware.

    python -m shelf.simulator --config config.example.json
    python -m shelf.simulator --config config.example.json --live shelf_events.jsonl

Each scenario feeds realistic weight events (with sensor noise) and camera results into the
engine, then checks the engine reached the expected conclusion.

With --live, the scenarios play out in real time instead, over and over, and each resulting shelf
event is appended to the given file exactly as the Shelf Service writes it. The store dashboard
reads that file, so its shelf view moves during a demo without any hardware.
"""

import argparse
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .config import load_catalogue
from .fusion import FusionSettings, ShelfEngine
from .models import EventType, ImageResult, WeightEvent
from .state import ShelfState


@dataclass
class Step:
    zone_id: str
    delta_g: float
    image: Optional[ImageResult] = None
    after_s: float = 2.0


@dataclass
class Scenario:
    name: str
    steps: List[Step]
    expect: List[EventType]


def unsure() -> ImageResult:
    return ImageResult(None, 0.0)


def sees(label: str, confidence: float = 0.9) -> ImageResult:
    return ImageResult(label, confidence)


SCENARIOS = [
    Scenario("Single pick, camera agrees",
             [Step("A1", -2012, sees("maize_flour_2kg"))], [EventType.PICK]),
    Scenario("Two identical items picked together",
             [Step("A1", -4024, unsure())], [EventType.PICK]),
    Scenario("Similar-weight products, camera decides",
             [Step("A2", -1008, sees("rice_1kg", 0.88))], [EventType.PICK]),
    Scenario("Similar-weight products, camera unsure -> review",
             [Step("A2", -1008, unsure())], [EventType.ANOMALY]),
    Scenario("Picked up and put back",
             [Step("B1", -528, unsure()), Step("B1", 528, unsure())], [EventType.PICK, EventType.RETURN]),
    Scenario("Moved to another shelf within 10 s",
             [Step("B1", -528, sees("milk_500ml")), Step("A2", 528, unsure(), after_s=6)],
             [EventType.PICK, EventType.MOVED]),
    Scenario("Returned to the wrong shelf later",
             [Step("B2", 935, sees("cooking_oil_1l")), Step("A1", 935, sees("cooking_oil_1l", 0.92), after_s=60)],
             [EventType.RETURN, EventType.MISPLACED]),
    Scenario("Unknown object placed on shelf",
             [Step("A1", 250, unsure())], [EventType.ANOMALY]),
    Scenario("Camera strongly disagrees with weight",
             [Step("B1", -528, sees("bread_400g", 0.95))], [EventType.ANOMALY]),
]


def run(config_path: str, noise_g: float = 2.0, seed: int = 7, verbose: bool = True) -> int:
    rng = random.Random(seed)
    products, zones = load_catalogue(config_path)
    failures = 0
    for scenario in SCENARIOS:
        engine, state = ShelfEngine(products, zones, FusionSettings(sensor_noise_g=noise_g)), ShelfState()
        ts, got = datetime(2026, 9, 23, 10, 0, tzinfo=timezone(timedelta(hours=3))), []
        weights = {z.zone_id: 5000.0 for z in zones}
        for i, step in enumerate(scenario.steps):
            ts += timedelta(seconds=step.after_s)
            before = weights[step.zone_id] + rng.gauss(0, noise_g)
            weights[step.zone_id] += step.delta_g
            after = weights[step.zone_id] + rng.gauss(0, noise_g)
            ev = WeightEvent("sim", step.zone_id, f"sim-{i}", round(before, 1), round(after, 1), ts)
            for r in engine.process(ev, step.image):
                state.apply(r)
                got.append(r)
        ok = [e.event_type for e in got] == scenario.expect
        failures += not ok
        if verbose:
            print(f"{'PASS' if ok else 'FAIL'}  {scenario.name}")
            for e in got:
                where = f"{e.from_zone_id}->{e.zone_id}" if e.from_zone_id else e.zone_id
                flags = " ".join(f for f, on in (("ALERT", e.alert), ("REVIEW", e.needs_review)) if on)
                print(f"      {e.event_type.value:<9} {where:<7} {e.product_id or '-':<16} qty={e.qty} "
                      f"conf={e.confidence:.2f} {flags} {e.note}".rstrip())
    if verbose:
        print(f"\n{len(SCENARIOS) - failures}/{len(SCENARIOS)} scenarios behaved as expected")
    return failures


def live(config_path: str, out_path: str, interval_s: float = 8.0, noise_g: float = 2.0, seed: int = 7,
         loops: Optional[int] = None, sleep=time.sleep) -> int:
    """Play the scenarios in real time, appending each shelf event to out_path. Returns events written.

    Steps within a scenario keep their own spacing (capped at interval_s, so a "moved" item still
    lands inside the engine's moved window); scenarios are interval_s apart. loops=None runs forever.
    """
    rng = random.Random(seed)
    products, zones = load_catalogue(config_path)
    engine = ShelfEngine(products, zones, FusionSettings(sensor_noise_g=noise_g))
    weights = {z.zone_id: 5000.0 for z in zones}
    written, n, loop = 0, 0, 0
    while loops is None or loop < loops:
        for scenario in SCENARIOS:
            for i, step in enumerate(scenario.steps):
                if i:
                    sleep(min(step.after_s, interval_s))
                before = weights[step.zone_id] + rng.gauss(0, noise_g)
                weights[step.zone_id] += step.delta_g
                after = weights[step.zone_id] + rng.gauss(0, noise_g)
                n += 1
                ev = WeightEvent("sim", step.zone_id, f"live-{n}", round(before, 1), round(after, 1),
                                 datetime.now(timezone.utc))
                results = engine.process(ev, step.image)
                with open(out_path, "a", encoding="utf-8") as f:
                    for r in results:
                        f.write(json.dumps(r.to_dict()) + "\n")
                written += len(results)
            sleep(interval_s)
        loop += 1
    return written


def main():
    parser = argparse.ArgumentParser(description="Run simulated shelf scenarios")
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--noise", type=float, default=2.0, help="Sensor noise, grams (standard deviation)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--live", metavar="EVENTS_FILE",
                        help="Play the scenarios in real time, forever, appending shelf events to this file")
    parser.add_argument("--interval", type=float, default=8.0, help="Seconds between scenarios in --live mode")
    args = parser.parse_args()
    if args.live:
        print(f"Writing live shelf events to {args.live} every {args.interval:g} s. Ctrl+C to stop.", flush=True)
        live(args.config, args.live, args.interval, args.noise, args.seed)
        return
    raise SystemExit(1 if run(args.config, args.noise, args.seed) else 0)


if __name__ == "__main__":
    main()
