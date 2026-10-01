"""The live shelf signal: what shoppers in the store have picked up in the last few minutes.

The Shelf Service writes one JSON line per shelf event (services/shelf/shelf/models.py, ShelfEvent). A pick
that hasn't been put back yet is probably in someone's basket, so the storefront doesn't promise it online.
Shelf events never change committed stock; this only makes the online promise more careful.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# Back on a shelf: put back, moved to another zone, or left on the wrong shelf.
PUT_BACK = ("return", "moved", "misplaced")


def read_recent_lines(path: str, limit: int = 2000) -> List[dict]:
    """The last `limit` events, oldest first. Reads from the end, so a long log stays cheap to poll."""
    if not path or not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = pos = f.tell()
        data = b""
        while pos > 0 and data.count(b"\n") <= limit:
            pos = max(0, pos - 64 * 1024)
            f.seek(pos)
            data = f.read(end - pos)
    lines = data.splitlines()
    if pos > 0:
        lines = lines[1:]  # may be cut in half
    events = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue  # a half-written last line
    return events


def picked_up(events: List[dict], window_min: int, now: Optional[datetime] = None) -> Dict[str, int]:
    """Per product: picked up in the last window_min minutes and not put back (never below zero)."""
    since = (now or datetime.now(timezone.utc)) - timedelta(minutes=window_min)
    net: Dict[str, int] = defaultdict(int)
    for e in events:
        pid, kind = e.get("product_id"), e.get("event_type")
        if not pid:
            continue
        try:
            ts = datetime.fromisoformat(e["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < since:
            continue
        qty = int(e.get("qty") or 0)
        if kind == "pick":
            net[pid] += qty
        elif kind in PUT_BACK:
            net[pid] -= qty
    return {pid: n for pid, n in net.items() if n > 0}
