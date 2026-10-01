from shelf.models import EventType, ImageResult

SURE = 0.9


def kinds(events):
    return [e.event_type for e in events]


def test_pick_with_camera_agreement(engine, ev):
    [e] = engine.process(ev("A1", -2012), ImageResult("maize_flour_2kg", SURE))
    assert (e.event_type, e.product_id, e.qty, e.confidence) == (EventType.PICK, "maize-flour-2kg", 1, 0.95)


def test_pick_without_camera_uses_weight_with_lower_confidence(engine, ev):
    [e] = engine.process(ev("A1", -4024), None)
    assert (e.event_type, e.qty, e.confidence) == (EventType.PICK, 2, 0.75)


def test_similar_weights_resolved_by_camera(engine, ev):
    [e] = engine.process(ev("A2", -1008), ImageResult("sugar_1kg", 0.8))
    assert (e.event_type, e.product_id) == (EventType.PICK, "sugar-1kg")


def test_similar_weights_camera_unsure_goes_to_review(engine, ev):
    [e] = engine.process(ev("A2", -1008), ImageResult(None, 0.0))
    assert e.event_type is EventType.ANOMALY and e.needs_review
    assert set(e.candidates) == {"sugar-1kg", "rice-1kg"}


def test_camera_below_minimum_confidence_is_ignored(engine, ev):
    [e] = engine.process(ev("A2", -1008), ImageResult("sugar_1kg", 0.4))
    assert e.event_type is EventType.ANOMALY


def test_strong_camera_disagreement_goes_to_review(engine, ev):
    [e] = engine.process(ev("B1", -528), ImageResult("bread_400g", 0.95))
    assert e.event_type is EventType.ANOMALY


def test_weak_camera_disagreement_does_not_overrule_weight(engine, ev):
    [e] = engine.process(ev("B1", -528), ImageResult("bread_400g", 0.7))
    assert (e.event_type, e.product_id) == (EventType.PICK, "milk-500ml")


def test_pick_then_put_back(engine, ev):
    out = engine.process(ev("B1", -528)) + engine.process(ev("B1", 528))
    assert kinds(out) == [EventType.PICK, EventType.RETURN]


def test_moved_between_zones_within_window(engine, ev):
    engine.process(ev("B1", -528, at_s=0))
    [e] = engine.process(ev("A2", 528, at_s=6))
    assert (e.event_type, e.from_zone_id, e.zone_id, e.product_id) == (EventType.MOVED, "B1", "A2", "milk-500ml")
    assert e.alert  # milk is not assigned to A2


def test_moved_window_expires(engine, ev):
    engine.process(ev("B1", -528, at_s=0))
    [e] = engine.process(ev("A2", 528, at_s=30), ImageResult("milk_500ml", SURE))
    assert e.event_type is EventType.MISPLACED


def test_misplaced_item_identified_by_camera_alerts(engine, ev):
    [e] = engine.process(ev("A1", 935), ImageResult("cooking_oil_1l", 0.92))
    assert (e.event_type, e.product_id, e.alert) == (EventType.MISPLACED, "cooking-oil-1l", True)


def test_misplaced_by_weight_only_needs_review_without_alert(engine, ev):
    [e] = engine.process(ev("A1", 935))
    assert e.event_type is EventType.MISPLACED and e.needs_review and not e.alert


def test_unknown_object_is_anomaly(engine, ev):
    [e] = engine.process(ev("A1", 250))
    assert e.event_type is EventType.ANOMALY


def test_noise_below_minimum_is_ignored(engine, ev):
    assert engine.process(ev("A1", -3)) == []


def test_duplicate_event_is_ignored(engine, ev):
    first = ev("A1", -2012, event_id="shelf-A-000001")
    assert len(engine.process(first)) == 1
    assert engine.process(first) == []


def test_unknown_zone_is_anomaly(engine, ev):
    [e] = engine.process(ev("Z9", -100))
    assert e.event_type is EventType.ANOMALY and e.note == "unknown zone"
