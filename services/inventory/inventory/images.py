"""Product photos, added by store staff from the dashboard and shown in the online shop and Scan & Go.

Every upload is opened and re-encoded: a phone photo of several megabytes becomes a JPEG of at most
MAX_SIDE pixels on its longest side (typically 60-150 kB), so the shop stays fast on mobile data. Re-encoding
also drops the photo's metadata (EXIF), which can include where it was taken. Anything that isn't a picture
is refused.
"""

import io
from typing import Optional, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 12 * 1024 * 1024  # bytes; a large phone photo
MAX_SIDE = 900  # pixels
QUALITY = 82
FORMATS = {"JPEG", "PNG", "WEBP", "MPO", "GIF"}  # MPO: some phones' JPEGs

Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse "decompression bombs" (a tiny file that opens huge)


class BadImage(ValueError):
    pass


def prepare(data: bytes) -> Tuple[bytes, int, int]:
    """Uploaded bytes -> (JPEG bytes, width, height). Raises BadImage."""
    if not data:
        raise BadImage("No picture was sent")
    if len(data) > MAX_UPLOAD:
        raise BadImage(f"That picture is too large ({len(data) // (1024 * 1024)} MB). The limit is {MAX_UPLOAD // (1024 * 1024)} MB.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in FORMATS:
                raise BadImage(f"Use a JPEG, PNG or WebP picture (this one is {img.format or 'unknown'})")
            img = ImageOps.exif_transpose(img)  # phones store "rotate me" in the metadata we're about to drop
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                flat = Image.new("RGB", img.size, (255, 255, 255))
                flat.paste(img, mask=img.split()[-1])
                img = flat
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, "JPEG", quality=QUALITY, optimize=True, progressive=True)
            return out.getvalue(), img.width, img.height
    except BadImage:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as e:
        raise BadImage("That file isn't a picture we can read. Use a JPEG, PNG or WebP photo.") from e


def etag(version: Optional[str]) -> str:
    return f'"{version}"' if version else '""'
