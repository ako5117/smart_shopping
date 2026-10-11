"""Product photos from the dashboard: any staff member can add one, only managers remove one."""
MARY = {"X-Staff-User": "mary", "X-Staff-Role": "staff"}
ADRIAN = {"X-Staff-User": "adrian", "X-Staff-Role": "manager"}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def test_floor_staff_add_a_photo_and_it_is_served(client, fake):
    r = client.put("/api/products/milk-500ml/image", content=JPEG, headers={**MARY, "Content-Type": "image/jpeg"})
    assert r.status_code == 200 and fake.photos["milk-500ml"] == (JPEG, "mary")
    got = client.get("/api/products/milk-500ml/image", headers=MARY)
    assert got.status_code == 200 and got.content == JPEG and got.headers["etag"] == '"v1"'
    assert client.get("/api/products/milk-500ml/image", headers={**MARY, "If-None-Match": '"v1"'}).status_code == 304


def test_bad_file_and_missing_photo(client):
    r = client.put("/api/products/milk-500ml/image", content=b"%PDF", headers={**MARY, "Content-Type": "application/pdf"})
    assert r.status_code == 422 and "picture" in r.json()["detail"]
    assert client.get("/api/products/sugar-1kg/image").status_code == 404


def test_only_managers_remove_photos(client, fake):
    client.put("/api/products/milk-500ml/image", content=JPEG, headers=MARY)
    assert client.delete("/api/products/milk-500ml/image", headers=MARY).status_code == 403
    assert client.delete("/api/products/milk-500ml/image", headers=ADRIAN).json() == {"removed": True}
    assert "milk-500ml" not in fake.photos


def test_inventory_down(client, fake):
    fake.down = True
    assert client.put("/api/products/milk-500ml/image", content=JPEG, headers=MARY).status_code == 502
    assert client.get("/api/products/milk-500ml/image").status_code == 502
