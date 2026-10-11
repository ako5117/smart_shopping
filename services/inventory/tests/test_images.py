"""Product photos: stored resized and re-encoded, served with a version for caching."""
import io

import pytest
from PIL import Image

from inventory import images
from tests.conftest import STORE  # noqa: F401  (fixtures)
from tests.test_api import client


def photo(w=3000, h=2000, fmt="JPEG", mode="RGB", exif=True) -> bytes:
    img = Image.new(mode, (w, h), (200, 30, 30) if mode == "RGB" else (200, 30, 30, 120))
    out = io.BytesIO()
    if exif and fmt == "JPEG":
        ex = Image.Exif()
        ex[0x010F] = "PhoneMaker"  # camera make
        ex[0x0112] = 6  # "rotate 90° when shown"
        img.save(out, fmt, exif=ex.tobytes())
    else:
        img.save(out, fmt)
    return out.getvalue()


def test_a_big_phone_photo_is_shrunk_turned_upright_and_stripped():
    jpeg, w, h = images.prepare(photo())
    assert max(w, h) == images.MAX_SIDE and (w, h) == (600, 900)  # orientation 6: the 3000x2000 photo stands upright
    with Image.open(io.BytesIO(jpeg)) as out:
        assert out.format == "JPEG" and not out.getexif()  # no camera details, no location
    assert len(jpeg) < 200_000


def test_png_with_transparency_becomes_a_jpeg_on_white():
    jpeg, w, h = images.prepare(photo(400, 300, "PNG", "RGBA"))
    with Image.open(io.BytesIO(jpeg)) as out:
        assert out.mode == "RGB" and (w, h) == (400, 300)


@pytest.mark.parametrize("data", [b"", b"%PDF-1.4 not a picture", b"\x89PNG\r\n\x1a\n" + b"0" * 50])
def test_not_a_picture_is_refused(data):
    with pytest.raises(images.BadImage):
        images.prepare(data)


def test_too_large_is_refused():
    with pytest.raises(images.BadImage, match="too large"):
        images.prepare(b"0" * (images.MAX_UPLOAD + 1))


def test_set_replace_and_delete(inv):
    from tests.conftest import MILK
    first = inv.set_image(MILK, photo(800, 800), "mary")
    assert inv.image(MILK)["updated_by"] == "mary"
    p = next(p for p in inv.products() if p["product_id"] == MILK)
    assert p["image_version"] == first["image_version"]
    second = inv.set_image(MILK, photo(500, 500), "john")
    assert second["image_version"] > first["image_version"] and inv.image(MILK)["width"] == 500
    inv.delete_image(MILK)
    assert next(p for p in inv.products() if p["product_id"] == MILK)["image_version"] is None


def test_photo_for_unknown_product(inv):
    from inventory.service import NotFound
    with pytest.raises(NotFound):
        inv.set_image("nope", photo(10, 10))


def test_photos_over_http():
    c = client()
    r = c.put("/products/milk-500ml/image", content=photo(1200, 900), headers={"Content-Type": "image/jpeg", "X-Staff-User": "mary"})
    assert r.status_code == 200 and (r.json()["width"], r.json()["height"]) == (675, 900)  # upright: the photo says "rotate"
    version = c.get("/products").json()[0]["image_version"]
    got = c.get("/products/milk-500ml/image")
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg" and got.headers["etag"] == f'"{version}"'
    assert c.get("/products/milk-500ml/image", headers={"If-None-Match": f'"{version}"'}).status_code == 304
    assert c.put("/products/milk-500ml/image", content=b"hello").status_code == 422
    assert c.put("/products/nope/image", content=photo(10, 10)).status_code == 404
    assert c.delete("/products/milk-500ml/image").status_code == 204
    assert c.get("/products/milk-500ml/image").status_code == 404
