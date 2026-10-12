"""Profile photos (optional): cropped to a square and re-encoded as a small JPEG.

Re-encoding drops the photo's metadata (EXIF), which can include where it was taken. Anything that isn't a
picture is refused.
"""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 12 * 1024 * 1024  # bytes
SIDE = 400  # pixels, square
QUALITY = 82
FORMATS = {"JPEG", "PNG", "WEBP", "MPO", "GIF"}

Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse "decompression bombs"


class BadImage(ValueError):
    pass


def prepare(data: bytes) -> bytes:
    """Uploaded bytes -> a square JPEG of at most SIDE pixels. Raises BadImage."""
    if not data:
        raise BadImage("No picture was sent")
    if len(data) > MAX_UPLOAD:
        raise BadImage(f"That picture is too large. The limit is {MAX_UPLOAD // (1024 * 1024)} MB.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in FORMATS:
                raise BadImage("Use a JPEG, PNG or WebP picture")
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGBA")
                flat = Image.new("RGB", img.size, (255, 255, 255))
                flat.paste(img, mask=img.split()[-1])
                img = flat
            side = min(img.size)
            img = ImageOps.fit(img, (min(side, SIDE), min(side, SIDE)), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, "JPEG", quality=QUALITY, optimize=True)
            return out.getvalue()
    except BadImage:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as e:
        raise BadImage("That file isn't a picture we can read. Use a JPEG, PNG or WebP photo.") from e


def prepare_snapshot(data: bytes, max_side: int = 1280) -> bytes:
    """A photo or screenshot sent with a problem report: at most max_side pixels, re-encoded, metadata dropped."""
    if not data:
        raise BadImage("No picture was sent")
    if len(data) > MAX_UPLOAD:
        raise BadImage(f"That picture is too large. The limit is {MAX_UPLOAD // (1024 * 1024)} MB.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in FORMATS:
                raise BadImage("Use a JPEG, PNG or WebP picture")
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGBA")
                flat = Image.new("RGB", img.size, (255, 255, 255))
                flat.paste(img, mask=img.split()[-1])
                img = flat
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, "JPEG", quality=QUALITY, optimize=True)
            return out.getvalue()
    except BadImage:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as e:
        raise BadImage("That file isn't a picture we can read. Use a JPEG, PNG or WebP photo.") from e
