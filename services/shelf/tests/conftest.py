import os
from datetime import datetime, timedelta, timezone

import pytest

from shelf.config import load_catalogue
from shelf.fusion import ShelfEngine
from shelf.models import WeightEvent

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config.example.json")
T0 = datetime(2026, 9, 23, 10, 0, tzinfo=timezone(timedelta(hours=3)))


@pytest.fixture
def catalogue():
    return load_catalogue(CONFIG)


@pytest.fixture
def engine(catalogue):
    products, zones = catalogue
    return ShelfEngine(products, zones)


class Events:
    """Builds weight events with increasing ids and timestamps."""

    def __init__(self):
        self.n = 0

    def __call__(self, zone, delta, at_s=None, device="shelf-A", event_id=None):
        self.n += 1
        ts = T0 + timedelta(seconds=self.n * 2 if at_s is None else at_s)
        return WeightEvent(device, zone, event_id or f"{device}-{self.n:06d}", 5000.0, 5000.0 + delta, ts)


@pytest.fixture
def ev():
    return Events()
