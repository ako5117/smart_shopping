"""Live view of what is on each shelf zone. Provisional: committed stock lives in the Inventory Service ledger."""

from collections import defaultdict
from typing import Dict, List, Tuple

from .models import EventType, ShelfEvent


class ShelfState:
    def __init__(self):
        self._qty: Dict[Tuple[str, str], int] = defaultdict(int)

    def set_count(self, zone_id: str, product_id: str, qty: int) -> None:
        """Set after a restock scan or a manual count."""
        self._qty[(zone_id, product_id)] = qty

    def apply(self, event: ShelfEvent) -> None:
        if event.product_id is None:
            return
        key = (event.zone_id, event.product_id)
        if event.event_type is EventType.PICK:
            self._qty[key] -= event.qty
        elif event.event_type in (EventType.RETURN, EventType.MISPLACED, EventType.MOVED):
            # For MOVED, the earlier PICK already removed the items from the source zone.
            self._qty[key] += event.qty

    def count(self, zone_id: str, product_id: str) -> int:
        return self._qty[(zone_id, product_id)]

    def low(self, threshold: int) -> List[Tuple[str, str, int]]:
        return sorted((z, p, q) for (z, p), q in self._qty.items() if q <= threshold)
