"""Simulated shelf activity, for developing and demonstrating the Shelf Service without hardware.

    python -m shelf.simulator --config config.example.json

Each scenario feeds realistic weight events (with sensor noise) and camera results into the
engine, then checks the engine reached the expected conclusion.
"""

import argparse
import random
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


def main():
    parser = argparse.ArgumentParser(description="Run simulated shelf scenarios")
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--noise", type=float, default=2.0, help="Sensor noise, grams (standard deviation)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    raise SystemExit(1 if run(args.config, args.noise, args.seed) else 0)


if __name__ == "__main__":
    main()
