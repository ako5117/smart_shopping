import json

import pytest

from shelf.app import build_handler
from shelf.config import load_catalogue
from shelf.messages import parse_weight_message
from shelf.models import EventType, ImageResult
from shelf.simulator import run
from shelf.state import ShelfState

TOPIC = "store/001/shelf/A/zone/A1/weight"
PAYLOAD = {"device_id": "shelf-A", "zone_id": "A1", "event_id": "shelf-A-000153",
           "weight_before_g": 6036.0, "weight_after_g": 4024.0, "delta_g": -2012.0,
           "settled_ms": 850, "ts": "2026-09-23T10:14:05+03:00"}


def test_state_tracks_picks_returns_and_moves(engine, ev):
    state = ShelfState()
    state.set_count("B1", "milk-500ml", 10)
    for e in engine.process(ev("B1", -1056, at_s=0)) + engine.process(ev("B1", 528, at_s=5)):
        state.apply(e)
    assert state.count("B1", "milk-500ml") == 9
    for e in engine.process(ev("B1", -528, at_s=10)) + engine.process(ev("A2", 528, at_s=14)):
        state.apply(e)
    assert state.count("B1", "milk-500ml") == 8 and state.count("A2", "milk-500ml") == 1
    assert ("B1", "milk-500ml", 8) in state.low(threshold=8)


def test_parse_weight_message():
    e = parse_weight_message(TOPIC, json.dumps(PAYLOAD).encode())
    assert (e.zone_id, e.event_id, e.delta_g) == ("A1", "shelf-A-000153", -2012.0)


def test_parse_rejects_zone_mismatch_and_bad_topic():
    with pytest.raises(ValueError):
        parse_weight_message("store/001/shelf/A/zone/A2/weight", json.dumps(PAYLOAD).encode())
    with pytest.raises(ValueError):
        parse_weight_message("store/001/other", json.dumps(PAYLOAD).encode())


def test_handler_end_to_end(engine, tmp_path):
    out = tmp_path / "events.jsonl"
    handle = build_handler(engine, ShelfState(), cameras={}, out_path=str(out))
    [e] = handle(TOPIC, json.dumps(PAYLOAD).encode())
    assert (e.event_type, e.qty) == (EventType.PICK, 1)
    assert json.loads(out.read_text().strip())["product_id"] == "maize-flour-2kg"
    assert handle(TOPIC, b"not json") == []


def test_config_rejects_unknown_product(tmp_path):
    bad = {"products": [], "zones": [{"zone_id": "A1", "shelf_id": "A", "product_ids": ["ghost"]}]}
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="ghost"):
        load_catalogue(str(p))


def test_all_simulated_scenarios_pass(catalogue):
    from tests.conftest import CONFIG
    assert run(CONFIG, noise_g=2.0, seed=7, verbose=False) == 0
