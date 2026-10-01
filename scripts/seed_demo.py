"""Load demo products, prices and stock into a running Inventory Service.

    python scripts/seed_demo.py --url http://localhost/inventory --user staff --password '...'
    python scripts/seed_demo.py --url http://localhost:8010          # Inventory run directly, no login

Uses the shelf catalogue (services/shelf/config.example.json), so product codes match the shelf zones.
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost/inventory", help="Inventory Service base URL")
    parser.add_argument("--user", default="staff")
    parser.add_argument("--password", help="Staff password, when going through the proxy")
    parser.add_argument("--store", default="001")
    parser.add_argument("--catalogue", default=str(ROOT / "services/shelf/config.example.json"))
    args = parser.parse_args(argv)

    auth = (args.user, args.password) if args.password else None
    client = httpx.Client(base_url=args.url.rstrip("/"), auth=auth, timeout=15.0)
    products = json.loads(Path(args.catalogue).read_text())["products"]
    for p in products:
        pid = p["product_id"]
        r = client.put(f"/products/{pid}", json={"product_id": pid, "ean13": p["ean13"], "name": p["name"],
                                                  "price_kes": PRICES_KES.get(pid)})
        r.raise_for_status()
        if pid in STOCK:
            r = client.post("/restocks", json={"store_id": args.store, "ean13": p["ean13"], "qty": STOCK[pid],
                                               "scan_id": f"demo-seed-{pid}"})
            r.raise_for_status()
        print(f"{p['name']:<20} KES {PRICES_KES.get(pid, '-'):>4}  stock {r.json().get('stock', '-')}")
    print(f"Loaded {len(products)} products into store {args.store}.")


if __name__ == "__main__":
    main()
