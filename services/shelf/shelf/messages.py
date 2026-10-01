"""Parsing weight events published by shelf nodes over MQTT (format: docs/sensor-logic.md, Step 1)."""

import json
from datetime import datetime, timezone

from .models import WeightEvent

TOPIC_FILTER = "store/+/shelf/+/zone/+/weight"


def parse_weight_message(topic: str, payload: bytes) -> WeightEvent:
    parts = topic.split("/")
    if len(parts) != 7 or parts[0] != "store" or parts[2] != "shelf" or parts[4] != "zone" or parts[6] != "weight":
        raise ValueError(f"Unexpected topic: {topic}")
    data = json.loads(payload)
    zone_id = data.get("zone_id") or parts[5]
    if zone_id != parts[5]:
        raise ValueError(f"zone_id {zone_id!r} does not match topic zone {parts[5]!r}")
    ts = datetime.fromisoformat(data["ts"]) if data.get("ts") else datetime.now(timezone.utc)
    return WeightEvent(
        device_id=str(data["device_id"]),
        zone_id=zone_id,
        event_id=str(data["event_id"]),
        weight_before_g=float(data["weight_before_g"]),
        weight_after_g=float(data["weight_after_g"]),
        ts=ts,
    )
