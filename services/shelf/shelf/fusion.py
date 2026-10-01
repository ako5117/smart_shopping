"""Steps 2-4 of docs/sensor-logic.md: combine a weight change and a camera result into shelf events.

Weight decides how many. The camera decides which one. Neither is trusted alone.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .matching import weight_candidates
from .models import EventType, ImageResult, Product, ShelfEvent, WeightEvent, Zone


@dataclass(frozen=True)
class FusionSettings:
    min_delta_g: float = 5.0             # changes smaller than this are noise
    sensor_noise_g: float = 2.0          # standard deviation of an idle load cell reading; measure on the bench
    camera_min_confidence: float = 0.6   # below this the camera counts as "unsure"
    camera_strong_confidence: float = 0.85  # at or above this the camera can overrule a single weight fit
    moved_window_s: float = 10.0         # pick in one zone + matching increase in another within this = moved
    alert_min_confidence: float = 0.7    # misplaced items below this are recorded but do not alert


class ShelfEngine:
    def __init__(self, products: Iterable[Product], zones: Iterable[Zone], settings: FusionSettings = FusionSettings()):
        self.settings = settings
        self.products: Dict[str, Product] = {p.product_id: p for p in products}
        self.by_label: Dict[str, Product] = {p.classifier_label: p for p in self.products.values()}
        self.zones: Dict[str, Zone] = {z.zone_id: z for z in zones}
        self._seen: Set[Tuple[str, str]] = set()
        self._recent_picks: List[ShelfEvent] = []

    # ------------------------------------------------------------------ public

    def process(self, event: WeightEvent, image: Optional[ImageResult] = None) -> List[ShelfEvent]:
        key = (event.device_id, event.event_id)
        if key in self._seen:
            return []  # the node re-sent after a network drop
        self._seen.add(key)

        zone = self.zones.get(event.zone_id)
        if zone is None:
            return [self._anomaly(event, "unknown zone")]
        if abs(event.delta_g) < self.settings.min_delta_g:
            return []

        self._expire_picks(event)
        camera = self._camera_product(image)
        if event.delta_g < 0:
            result = self._decrease(event, zone, camera, image)
            if result.event_type is EventType.PICK:
                self._recent_picks.append(result)
            return [result]
        return [self._increase(event, zone, camera, image)]

    # ------------------------------------------------------------ decreases

    def _decrease(self, ev: WeightEvent, zone: Zone, camera: Optional[Product], image: Optional[ImageResult]) -> ShelfEvent:
        cands = weight_candidates(ev.delta_g, self._assigned(zone), self.settings.sensor_noise_g)
        if len(cands) == 1:
            c = cands[0]
            if self._camera_overrules(camera, image, c.product):
                return self._anomaly(ev, "camera strongly disagrees with weight", [c.product.product_id, camera.product_id])
            conf = 0.95 if camera and camera.product_id == c.product.product_id else 0.75
            return self._event(EventType.PICK, ev, c.product, c.qty, conf)
        if len(cands) > 1:
            chosen = next((c for c in cands if camera and c.product.product_id == camera.product_id), None)
            if chosen:
                return self._event(EventType.PICK, ev, chosen.product, chosen.qty, min(0.95, image.confidence))
            return self._anomaly(ev, "several products match the weight; camera unsure", [c.product.product_id for c in cands])
        return self._anomaly(ev, "no product assigned to this zone matches the weight change")

    # ------------------------------------------------------------ increases

    def _increase(self, ev: WeightEvent, zone: Zone, camera: Optional[Product], image: Optional[ImageResult]) -> ShelfEvent:
        moved = self._match_moved(ev, zone)
        if moved:
            return moved

        cands = weight_candidates(ev.delta_g, self._assigned(zone), self.settings.sensor_noise_g)
        if len(cands) == 1 and not self._camera_overrules(camera, image, cands[0].product):
            c = cands[0]
            conf = 0.95 if camera and camera.product_id == c.product.product_id else 0.75
            return self._event(EventType.RETURN, ev, c.product, c.qty, conf)
        if len(cands) > 1:
            chosen = next((c for c in cands if camera and c.product.product_id == camera.product_id), None)
            if chosen:
                return self._event(EventType.RETURN, ev, chosen.product, chosen.qty, min(0.95, image.confidence))
            return self._anomaly(ev, "several products match the weight; camera unsure", [c.product.product_id for c in cands])

        # Nothing assigned here explains it (or the camera overruled): a product from elsewhere?
        foreign = weight_candidates(ev.delta_g, [p for p in self.products.values() if p.product_id not in zone.product_ids],
                                    self.settings.sensor_noise_g)
        if camera:
            match = next((c for c in foreign if c.product.product_id == camera.product_id), None)
            if match:
                conf = min(0.95, image.confidence)
                e = self._event(EventType.MISPLACED, ev, match.product, match.qty, conf)
                e.alert = conf >= self.settings.alert_min_confidence
                return e
        if len(foreign) == 1:
            f = foreign[0]
            e = self._event(EventType.MISPLACED, ev, f.product, f.qty, 0.5, note="identified by weight only")
            e.needs_review = True
            return e
        return self._anomaly(ev, "weight increase not explained by any known product", [c.product.product_id for c in foreign])

    def _match_moved(self, ev: WeightEvent, zone: Zone) -> Optional[ShelfEvent]:
        for pick in reversed(self._recent_picks):
            if pick.zone_id == zone.zone_id:
                continue
            product = self.products[pick.product_id]
            # Same physical items, so only sensor noise separates the two changes: sqrt(2) more than one change.
            allowed = 3 * self.settings.sensor_noise_g * 2 + product.weight_tolerance_g * 0.5
            if abs(ev.delta_g - abs(pick.weight_delta_g)) <= allowed:
                self._recent_picks.remove(pick)
                misplaced = product.product_id not in zone.product_ids
                e = self._event(EventType.MOVED, ev, product, pick.qty, min(0.9, pick.confidence),
                                note="moved to a zone it is not assigned to" if misplaced else "")
                e.from_zone_id = pick.zone_id
                e.alert = misplaced and e.confidence >= self.settings.alert_min_confidence
                return e
        return None

    # -------------------------------------------------------------- helpers

    def _assigned(self, zone: Zone) -> List[Product]:
        return [self.products[pid] for pid in zone.product_ids if pid in self.products]

    def _camera_product(self, image: Optional[ImageResult]) -> Optional[Product]:
        if image and image.label and image.confidence >= self.settings.camera_min_confidence:
            return self.by_label.get(image.label)
        return None

    def _camera_overrules(self, camera: Optional[Product], image: Optional[ImageResult], weight_choice: Product) -> bool:
        return bool(camera and camera.product_id != weight_choice.product_id
                    and image.confidence >= self.settings.camera_strong_confidence)

    def _expire_picks(self, ev: WeightEvent) -> None:
        cutoff = ev.ts - timedelta(seconds=self.settings.moved_window_s)
        self._recent_picks = [p for p in self._recent_picks if p.ts >= cutoff]

    def _event(self, kind: EventType, ev: WeightEvent, product: Product, qty: int, conf: float, note: str = "") -> ShelfEvent:
        return ShelfEvent(kind, ev.zone_id, product.product_id, qty, ev.delta_g, conf, ev.event_id, ev.ts, note=note)

    def _anomaly(self, ev: WeightEvent, note: str, candidates: Optional[list] = None) -> ShelfEvent:
        return ShelfEvent(EventType.ANOMALY, ev.zone_id, None, 0, ev.delta_g, 0.0, ev.event_id, ev.ts,
                          needs_review=True, candidates=candidates or [], note=note)
