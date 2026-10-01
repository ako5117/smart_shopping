"""Background sync with the retailer's system.

    RETAILER_API_URL=https://retailer.example/api python -m inventory.worker

Every 30 s: send queued stock movements. Every hour: pull the retailer's stock and reconcile.
"""

import logging
import os
import time

from .db import Database
from .sync import HttpRetailerAdapter, drain_outbox, reconcile

log = logging.getLogger("inventory.worker")


def main():
    logging.basicConfig(level=logging.INFO)
    db = Database(os.getenv("INVENTORY_DB_PATH", "inventory.db"))
    adapter = HttpRetailerAdapter(os.environ["RETAILER_API_URL"], os.getenv("RETAILER_API_KEY", ""))
    stores = [s for s in os.getenv("STORE_IDS", "001").split(",") if s]
    last_reconcile = 0.0
    while True:
        result = drain_outbox(db, adapter)
        if result["sent"]:
            log.info("Sent %d movements, %d pending", result["sent"], result["pending"])
        if time.time() - last_reconcile > 3600 and result["pending"] == 0:
            for store in stores:
                try:
                    diffs = reconcile(db, store, adapter.stock_snapshot(store))
                    log.info("Reconciled store %s: %d differences", store, len(diffs))
                except Exception as e:
                    log.warning("Reconcile failed for store %s: %s", store, e)
            last_reconcile = time.time()
        time.sleep(30)


if __name__ == "__main__":
    main()
