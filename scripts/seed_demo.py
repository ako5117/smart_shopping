"""Load demo products, prices and stock into a running Inventory Service.

    python scripts/seed_demo.py --url http://localhost/inventory --user <manager login> --password '...'
    python scripts/seed_demo.py --url http://localhost:8010          # Inventory run directly, no login

Uses the shelf catalogue (services/shelf/config.example.json), so product codes match the shelf zones, plus a
few products kept off the sensor shelves. Brown sugar is sold out and brown bread nearly so, so the online shop
has something to suggest substitutes for.
Safe to run twice: products are updated in place and each restock has a fixed scan id.
"""

import argparse
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PRICES_KES = {"maize-flour-2kg": 210, "sugar-1kg": 180, "rice-1kg": 220,
              "milk-500ml": 65, "cooking-oil-1l": 340, "bread-400g": 70}
STOCK = {"maize-flour-2kg": 24, "sugar-1kg": 18, "rice-1kg": 30,
         "milk-500ml": 48, "cooking-oil-1l": 14, "bread-400g": 12}
CATEGORIES = {"maize-flour-2kg": "Flour & rice", "rice-1kg": "Flour & rice", "sugar-1kg": "Sugar",
              "milk-500ml": "Milk", "cooking-oil-1l": "Cooking oil", "bread-400g": "Bread"}
# Not on a sensor shelf: (product_id, EAN-13, name, price, category, stock)
EXTRA_PRODUCTS = [
    ("wheat-flour-2kg", "6161000000071", "Wheat flour 2 kg", 230, "Flour & rice", 20),
    ("basmati-rice-1kg", "6161000000125", "Basmati rice 1 kg", 320, "Flour & rice", 8),
    ("brown-sugar-1kg", "6161000000095", "Brown sugar 1 kg", 200, "Sugar", 0),
    ("uht-milk-500ml", "6161000000088", "Long-life milk 500 ml", 75, "Milk", 24),
    ("brown-bread-400g", "6161000000101", "Brown bread 400 g", 75, "Bread", 3),
    ("sunflower-oil-1l", "6161000000118", "Sunflower oil 1 L", 390, "Cooking oil", 10),
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost/inventory", help="Inventory Service base URL")
    parser.add_argument("--user", help="A manager login, when going through the proxy")
    parser.add_argument("--password", help="Staff password, when going through the proxy")
    parser.add_argument("--store", default="001")
    parser.add_argument("--catalogue", default=str(ROOT / "services/shelf/config.example.json"))
    args = parser.parse_args(argv)

    auth = (args.user, args.password) if args.password else None
    client = httpx.Client(base_url=args.url.rstrip("/"), auth=auth, timeout=15.0)
    products = [(p["product_id"], p["ean13"], p["name"], PRICES_KES.get(p["product_id"]), CATEGORIES.get(p["product_id"], ""),
                 STOCK.get(p["product_id"], 0)) for p in json.loads(Path(args.catalogue).read_text())["products"]]
    for pid, ean13, name, price, category, stock in products + EXTRA_PRODUCTS:
        r = client.put(f"/products/{pid}", json={"product_id": pid, "ean13": ean13, "name": name,
                                                  "price_kes": price, "category": category})
        r.raise_for_status()
        if stock:
            r = client.post("/restocks", json={"store_id": args.store, "ean13": ean13, "qty": stock,
                                               "scan_id": f"demo-seed-{pid}"})
            r.raise_for_status()
        print(f"{name:<22} KES {price or '-':>4}  {category:<13} stock {r.json().get('stock', 0)}")
    print(f"Loaded {len(products) + len(EXTRA_PRODUCTS)} products into store {args.store}.")


if __name__ == "__main__":
    main()
