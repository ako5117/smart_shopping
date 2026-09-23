import json
from typing import List, Tuple

from .models import Product, Zone


def load_catalogue(path: str) -> Tuple[List[Product], List[Zone]]:
    """Load products and zone assignments from a JSON file (see config.example.json)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    products = [Product(**p) for p in data["products"]]
    zones = [Zone(z["zone_id"], z["shelf_id"], tuple(z["product_ids"])) for z in data["zones"]]
    known = {p.product_id for p in products}
    for z in zones:
        missing = set(z.product_ids) - known
        if missing:
            raise ValueError(f"Zone {z.zone_id} refers to unknown products: {sorted(missing)}")
    return products, zones
