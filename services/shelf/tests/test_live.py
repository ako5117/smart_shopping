import json
from pathlib import Path

from shelf.models import EventType
from shelf.simulator import SCENARIOS, live

CONFIG = str(Path(__file__).resolve().parent.parent / "config.example.json")


def test_live_writes_the_same_events_as_the_scenarios(tmp_path):
    out = tmp_path / "shelf_events.jsonl"
    sleeps = []
    written = live(CONFIG, str(out), interval_s=8, loops=1, sleep=sleeps.append)
    events = [json.loads(line) for line in out.read_text().splitlines()]
    assert written == len(events)
    assert [e["event_type"] for e in events] == [t.value for s in SCENARIOS for t in s.expect]
    assert {"zone_id", "product_id", "qty", "ts", "alert", "needs_review"} <= set(events[0])
    assert any(e["alert"] for e in events) and any(e["needs_review"] for e in events)
    assert max(sleeps) == 8 and len(sleeps) == len(SCENARIOS) + sum(len(s.steps) - 1 for s in SCENARIOS)


def test_live_keeps_appending(tmp_path):
    out = tmp_path / "shelf_events.jsonl"
    first = live(CONFIG, str(out), loops=1, sleep=lambda s: None)
    live(CONFIG, str(out), loops=1, sleep=lambda s: None)
    assert len(out.read_text().splitlines()) == 2 * first
    assert EventType.MOVED.value in out.read_text()
