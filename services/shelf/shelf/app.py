"""Run the Shelf Service against an MQTT broker.

    python -m shelf.app --config config.example.json --broker localhost

Each shelf event is printed as one JSON line and appended to --out (default shelf_events.jsonl).
"""

import argparse
import json
import logging
from typing import Dict, Optional

from .camera import UnsureClassifier, fetch_snapshot
from .config import load_catalogue
from .fusion import ShelfEngine
from .messages import TOPIC_FILTER, parse_weight_message
from .state import ShelfState

log = logging.getLogger("shelf")


def build_handler(engine: ShelfEngine, state: ShelfState, cameras: Dict[str, str], out_path: str, classifier=None):
    classifier = classifier or UnsureClassifier()

    def handle(topic: str, payload: bytes):
        try:
            event = parse_weight_message(topic, payload)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            log.warning("Ignoring bad message on %s: %s", topic, e)
            return []
        image = None
        shelf_id = topic.split("/")[3]
        if shelf_id in cameras:
            jpeg = fetch_snapshot(cameras[shelf_id])
            if jpeg:
                image = classifier.classify(event.zone_id, jpeg)
        results = engine.process(event, image)
        with open(out_path, "a", encoding="utf-8") as f:
            for r in results:
                state.apply(r)
                line = json.dumps(r.to_dict())
                print(line, flush=True)
                f.write(line + "\n")
        return results

    return handle


def main(argv: Optional[list] = None):
    parser = argparse.ArgumentParser(description="Smart Shopping Shelf Service")
    parser.add_argument("--config", required=True, help="Catalogue JSON (products and zones)")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--camera", action="append", default=[], metavar="SHELF_ID=URL",
                        help="Snapshot URL per shelf, e.g. A=http://192.168.1.50/capture")
    parser.add_argument("--out", default="shelf_events.jsonl")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    import paho.mqtt.client as mqtt  # imported here so the rest of the package has no MQTT dependency

    products, zones = load_catalogue(args.config)
    engine, state = ShelfEngine(products, zones), ShelfState()
    cameras = dict(c.split("=", 1) for c in args.camera)
    handle = build_handler(engine, state, cameras, args.out)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="shelf-service")
    if args.username:
        client.username_pw_set(args.username, args.password)
    client.on_connect = lambda c, u, f, rc, p=None: (log.info("Connected to %s", args.broker), c.subscribe(TOPIC_FILTER, qos=1))
    client.on_message = lambda c, u, msg: handle(msg.topic, msg.payload)
    client.connect(args.broker, args.port)
    client.loop_forever()


if __name__ == "__main__":
    main()
