import inspect
import io
import re

from PIL import Image
from runware import RunwareError

from vjhstudio import db, models
from vjhstudio.web.routes import assets as assets_routes


def _png(size: int = 64, color: tuple[int, int, int] = (1, 2, 3)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (size, size), color).save(b, "PNG")
    return b.getvalue()


def _asset_id(html: str) -> str:
    m = re.search(r'id="asset-(\d+)"', html)
    assert m, html
    return m.group(1)


def _file_url(html: str) -> str:
    m = re.search(r"/files/uploads/[\w.\-]+", html)
    assert m, html
    return m.group(0)


async def test_assets_handlers_sync_except_upload_and_push():
    """DB-only handlers must stay sync `def` (Starlette threadpools them); upload and
    push are the only two that await anything, so they alone are `async def`."""
    sync_fns = (
        assets_routes.assets_page,
        assets_routes.hx_assets,
        assets_routes.set_asset_tags,
        assets_routes.set_asset_notes,
        assets_routes.delete_asset,
        assets_routes.hx_assets_picker,
    )
    for fn in sync_fns:
        assert not inspect.iscoroutinefunction(fn), fn.__name__
    assert inspect.iscoroutinefunction(assets_routes.upload_assets)
    assert inspect.iscoroutinefunction(assets_routes.push_asset)


async def test_assets_page_renders(client):
    r = await client.get("/assets")
    assert r.status_code == 200
    assert "Assets" in r.text
    assert 'hx-post="/assets/upload"' in r.text
    assert 'hx-encoding="multipart/form-data"' in r.text
    assert 'id="asset-grid"' in r.text


async def test_header_has_assets_after_gallery(client):
    r = await client.get("/")
    gallery_i = r.text.index('href="/gallery"')
    assets_i = r.text.index('href="/assets"')
    assert gallery_i < assets_i


async def test_upload_two_pngs_and_a_duplicate_toasts(client):
    a = _png(64, (1, 2, 3))
    b = _png(64, (4, 5, 6))
    files = [
        ("files", ("a.png", a, "image/png")),
        ("files", ("b.png", b, "image/png")),
        ("files", ("a-dup.png", a, "image/png")),
    ]
    r = await client.post("/assets/upload", files=files, data={"tags": "fox"})
    assert r.status_code == 200
    assert r.text.count('class="asset-card"') == 2
    assert "already in your library" in r.text
    assert "a-dup.png" in r.text
    assert "fox" in r.text


async def test_upload_oversize_413(client):
    r = await client.post("/settings", data={"uploads.max_mb": "1"})
    assert r.status_code == 200
    big = b"\x89PNG\r\n" + b"\x00" * (2 * 1024 * 1024)
    r = await client.post("/assets/upload", files=[("files", ("big.png", big, "image/png"))])
    assert r.status_code == 422
    assert "exceeds" in r.text and "1 MB" in r.text


async def test_upload_oversize_is_refused_before_the_file_is_read(client, monkeypatch):
    """UploadFile.size is checked first, so a huge drop never lands in RAM."""
    from starlette.datastructures import UploadFile

    r = await client.post("/settings", data={"uploads.max_mb": "1"})
    assert r.status_code == 200

    async def boom(self, size: int = -1):
        raise AssertionError("an oversize upload must not be read into memory")

    monkeypatch.setattr(UploadFile, "read", boom)
    big = b"\x89PNG\r\n" + b"\x00" * (2 * 1024 * 1024)
    r = await client.post("/assets/upload", files=[("files", ("big.png", big, "image/png"))])
    assert r.status_code == 422
    assert "big.png: file exceeds 1 MB limit" in r.text


async def test_upload_unsupported_type_415(client):
    r = await client.post("/assets/upload", files=[("files", ("note.txt", b"hello", "text/plain"))])
    assert r.status_code == 422
    assert "unsupported content type" in r.text.lower()


async def test_upload_requires_api_key_not_required(client):
    """Uploading itself never needs the RunWare key -- only /push does."""
    r = await client.post("/assets/upload", files=[("files", ("a.png", _png(), "image/png"))])
    assert r.status_code == 200


async def test_tags_update(client):
    r = await client.post("/assets/upload", files=[("files", ("a.png", _png(), "image/png"))])
    aid = _asset_id(r.text)
    r2 = await client.post(f"/assets/{aid}/tags", data={"tags": "Fox, Animals, fox"})
    assert r2.status_code == 200
    assert "fox" in r2.text.lower() and "animals" in r2.text.lower()


async def test_notes_update(client):
    r = await client.post("/assets/upload", files=[("files", ("a.png", _png(), "image/png"))])
    aid = _asset_id(r.text)
    r2 = await client.post(f"/assets/{aid}/notes", data={"notes": "hero shot"})
    assert r2.status_code == 200
    assert "hero shot" in r2.text


async def test_unknown_asset_tags_notes_404(client):
    assert (await client.post("/assets/999999/tags", data={"tags": "x"})).status_code == 404
    assert (await client.post("/assets/999999/notes", data={"notes": "x"})).status_code == 404


async def test_delete_then_404_on_file_route(client):
    r = await client.post("/assets/upload", files=[("files", ("a.png", _png(), "image/png"))])
    aid = _asset_id(r.text)
    file_url = _file_url(r.text)
    r2 = await client.delete(f"/assets/{aid}")
    assert r2.status_code == 200 and r2.text == ""
    r3 = await client.get(file_url)
    assert r3.status_code == 404


async def test_delete_unknown_404(client):
    assert (await client.delete("/assets/999999")).status_code == 404


async def test_delete_refused_when_referenced_by_active_job(client, app):
    r = await client.post("/assets/upload", files=[("files", ("a.png", _png(), "image/png"))])
    aid = int(_asset_id(r.text))
    with db.session_scope(app.state.boot.session_factory) as s:
        s.add(
            models.Job(
                id="job-ref-1",
                project_id=1,
                kind="image",
                status=models.JobStatus.queued.value,
                model_air="runware:101@1",
                request_json={"seed_image_asset_id": aid},
            )
        )
    r2 = await client.delete(f"/assets/{aid}")
    assert r2.status_code == 409
    assert "error" in r2.json()


async def test_picker_returns_buttons(client):
    r = await client.post("/assets/upload", files=[("files", ("a.png", _png(), "image/png"))])
    aid = _asset_id(r.text)
    r2 = await client.get("/hx/assets/picker?kind=image&role=reference")
    assert r2.status_code == 200
    assert '<button type="button"' in r2.text
    assert f'data-asset-id="{aid}"' in r2.text
    assert 'data-role="reference"' in r2.text
    assert "data-thumb=" in r2.text and "data-name=" in r2.text


async def test_file_routes_reject_traversal(client):
    assert (await client.get("/files/uploads/..%2F..%2Fpyproject.toml")).status_code == 404
    assert (await client.get("/files/asset-thumbs/..%2F..%2Fpyproject.toml")).status_code == 404
    assert (await client.get("/files/uploads/does-not-exist.png")).status_code == 404
    assert (await client.get("/files/asset-thumbs/does-not-exist.jpg")).status_code == 404


async def test_push_without_api_key_422(client):
    r = await client.post("/assets/upload", files=[("files", ("a.png", _png(), "image/png"))])
    aid = _asset_id(r.text)
    r2 = await client.post(f"/assets/{aid}/push")
    assert r2.status_code == 422
    assert "api key" in r2.text.lower()


async def test_push_unknown_asset_404(client):
    assert (await client.post("/assets/999999/push")).status_code == 404


async def test_push_success_shows_badge(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["media_storage"] = [[{"mediaUUID": "uuid-1", "mediaURL": "http://x/1"}]]
    r = await client.post("/assets/upload", files=[("files", ("a.png", _png(), "image/png"))])
    aid = _asset_id(r.text)
    r2 = await client.post(f"/assets/{aid}/push")
    assert r2.status_code == 200
    assert "uploaded to runware" in r2.text.lower()


async def test_push_media_upload_error_422(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["media_storage"] = [RunwareError("invalidApiKey", "bad key")]
    r = await client.post("/assets/upload", files=[("files", ("a.png", _png(), "image/png"))])
    aid = _asset_id(r.text)
    r2 = await client.post(f"/assets/{aid}/push")
    assert r2.status_code == 422
    assert "rejected the api key" in r2.text.lower()


async def test_filters_kind_and_q(client):
    await client.post(
        "/assets/upload", files=[("files", ("castle.png", _png(1, (1, 2, 3)), "image/png"))]
    )
    await client.post(
        "/assets/upload", files=[("files", ("forest.png", _png(1, (9, 8, 7)), "image/png"))]
    )
    r = await client.get("/hx/assets?q=castle")
    assert r.text.count('class="asset-card"') == 1
    assert "castle.png" in r.text
    r = await client.get("/hx/assets?kind=video")
    assert r.text.count('class="asset-card"') == 0


def _bulk_assets(app, n: int) -> None:
    """Rows only: paging is about the grid, not about 50 real files on disk."""
    with db.session_scope(app.state.boot.session_factory) as s:
        for i in range(n):
            s.add(
                models.Asset(
                    filename=f"bulk{i:04d}.png",
                    original_name=f"bulk-{i}.png",
                    kind="image",
                    mime="image/png",
                    size_bytes=10,
                    sha256=f"{i:064d}",
                    tags=",bulk,",
                )
            )


async def test_assets_load_more_appends_instead_of_replacing(client, app):
    _bulk_assets(app, 50)
    page1 = await client.get("/assets")
    assert page1.status_code == 200
    assert page1.text.count('class="asset-card"') == 48
    assert 'id="asset-load-more"' in page1.text
    # the button swaps itself out, so the 48 cards already rendered stay put
    assert 'hx-target="this"' in page1.text

    page2 = await client.get("/hx/assets?page=2")
    assert page2.status_code == 200
    assert page2.text.count('class="asset-card"') == 2
    assert 'id="asset-grid"' not in page2.text
    assert 'id="asset-load-more"' not in page2.text  # nothing left to load
    assert "bulk-1.png" in page2.text and "bulk-49.png" not in page2.text


async def test_hx_assets_page_one_still_returns_the_whole_grid(client, app):
    _bulk_assets(app, 50)
    r = await client.get("/hx/assets?page=1")
    assert 'id="asset-grid"' in r.text and r.text.count('class="asset-card"') == 48
