"""Camera snapshot and classification.

The classifier is a stand-in until the trained product model exists. It reports "unsure",
so the engine decides on weight alone and routes ambiguous cases to review, which is the
safe default.
"""

from typing import Optional, Protocol

import urllib.request

from .models import ImageResult


class Classifier(Protocol):
    def classify(self, zone_id: str, jpeg: bytes) -> ImageResult: ...


class UnsureClassifier:
    def classify(self, zone_id: str, jpeg: bytes) -> ImageResult:
        return ImageResult(label=None, confidence=0.0)


def fetch_snapshot(url: str, timeout_s: float = 3.0) -> Optional[bytes]:
    """GET a JPEG from an ESP32-CAM. Returns None if the camera is unreachable, so shelf events still flow."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            return resp.read()
    except OSError:
        return None
