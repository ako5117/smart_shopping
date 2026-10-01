"""Step 2 of docs/sensor-logic.md: turn a weight change into candidate products and quantities."""

import math
from dataclasses import dataclass
from typing import Iterable, List

from .models import Product

MAX_QTY = 50  # a single settled change larger than this is treated as a restock or fault, not a pick


@dataclass(frozen=True)
class Candidate:
    product: Product
    qty: int
    error_g: float


def allowed_error_g(product: Product, qty: int, sensor_noise_g: float, k: float = 3.0) -> float:
    """How far a measured change may be from qty x unit weight and still count as a fit.

    Two independent error sources are combined:
    - product variation: units differ in weight; adds up over qty items -> tolerance x sqrt(qty)
    - sensor noise: a change is (after - before), two noisy readings -> noise x sqrt(2), allowed k standard deviations
    """
    product_part = product.weight_tolerance_g * math.sqrt(qty)
    sensor_part = k * sensor_noise_g * math.sqrt(2)
    return math.hypot(product_part, sensor_part)


def weight_candidates(delta_g: float, products: Iterable[Product], sensor_noise_g: float = 0.0) -> List[Candidate]:
    """Products whose unit weight explains |delta_g| as a whole number of items.

    fits = qty >= 1 and error <= allowed_error_g(product, qty, sensor_noise_g)
    Sorted best fit first (smallest error relative to the allowed error).
    """
    magnitude = abs(delta_g)
    fits = []
    for p in products:
        if p.unit_weight_g <= 0:
            continue
        n = magnitude / p.unit_weight_g
        qty = round(n)
        if qty < 1 or qty > MAX_QTY:
            continue
        error = abs(n - qty) * p.unit_weight_g
        allowed = allowed_error_g(p, qty, sensor_noise_g)
        if error <= allowed:
            fits.append((error / allowed if allowed else 0.0, Candidate(p, qty, error)))
    fits.sort(key=lambda x: x[0])
    return [c for _, c in fits]
