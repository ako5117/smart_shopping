from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


@dataclass(frozen=True)
class Product:
    product_id: str
    ean13: str
    name: str
    unit_weight_g: float
    weight_tolerance_g: float
    classifier_label: str


@dataclass(frozen=True)
class Zone:
    zone_id: str
    shelf_id: str
    product_ids: tuple  # planogram: products assigned to this zone


@dataclass(frozen=True)
class WeightEvent:
    """A settled weight change reported by a shelf node (see docs/sensor-logic.md, Step 1)."""
    device_id: str
    zone_id: str
    event_id: str
    weight_before_g: float
    weight_after_g: float
    ts: datetime

    @property
    def delta_g(self) -> float:
        return self.weight_after_g - self.weight_before_g


@dataclass(frozen=True)
class ImageResult:
    """What the camera classifier saw in the changed area. label is None when it could not tell."""
    label: Optional[str]
    confidence: float


class EventType(str, Enum):
    PICK = "pick"
    RETURN = "return"
    MOVED = "moved"
    MISPLACED = "misplaced"
    ANOMALY = "anomaly"


@dataclass
class ShelfEvent:
    event_type: EventType
    zone_id: str
    product_id: Optional[str]
    qty: int
    weight_delta_g: float
    confidence: float
    source_event_id: str
    ts: datetime
    from_zone_id: Optional[str] = None
    alert: bool = False
    needs_review: bool = False
    candidates: list = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "zone_id": self.zone_id,
            "product_id": self.product_id,
            "qty": self.qty,
            "weight_delta_g": round(self.weight_delta_g, 1),
            "confidence": round(self.confidence, 2),
            "source_event_id": self.source_event_id,
            "ts": self.ts.isoformat(),
            "from_zone_id": self.from_zone_id,
            "alert": self.alert,
            "needs_review": self.needs_review,
            "candidates": self.candidates,
            "note": self.note,
        }
