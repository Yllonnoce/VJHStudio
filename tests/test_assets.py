import io
from datetime import timedelta

import pytest
from PIL import Image
from runware import RunwareError

from tests.fakes.fake_runware import FakeRunware
from vjhstudio import boot, config, db, models
from vjhstudio.models import utcnow
from vjhstudio.services import assets


def _png(size=(64, 64)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(b, "PNG")
    return b.getvalue()


def _mp4() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + b"\0" * 100


@pytest.fixture
def booted(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    return paths, info.session_factory


def test_store_png_creates_row_dims_and_thumbnail(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, created = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        assert created is True
        assert asset.kind == "image"
        assert asset.width == 64 and asset.height == 64
        assert asset.mime == "image/png"
        assert asset.filename == f"{asset.sha256[:12]}.png"
        upload_path = paths.uploads / asset.filename
        assert upload_path.exists() and upload_path.read_bytes() == _png()
        thumb_path = paths.thumbs / f"asset-{asset.sha256[:12]}.jpg"
        assert thumb_path.exists()


def test_restoring_identical_bytes_dedupes(booted):
    paths, f = booted
    content = _png()
    with db.session_scope(f) as s:
        a1, created1 = assets.store_upload(
            s, paths, original_name="fox.png", content=content, mime="image/png"
        )
        id1 = a1.id
        assert created1 is True
    with db.session_scope(f) as s:
        a2, created2 = assets.store_upload(
            s, paths, original_name="fox-again.png", content=content, mime="image/png"
        )
        assert created2 is False
        assert a2.id == id1
    with db.session_scope(f) as s:
        assert s.query(models.Asset).count() == 1


def test_store_mp4_has_no_dims_or_thumbnail(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, created = assets.store_upload(
            s, paths, original_name="clip.mp4", content=_mp4(), mime="video/mp4"
        )
        assert created is True
        assert asset.kind == "video"
        assert asset.width is None and asset.height is None
        assert asset.filename == f"{asset.sha256[:12]}.mp4"
        assert (paths.uploads / asset.filename).exists()
        thumb_path = paths.thumbs / f"asset-{asset.sha256[:12]}.jpg"
        assert not thumb_path.exists()
        assert assets.thumb_rel(asset) is None


def test_unsupported_mime_raises_415(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        with pytest.raises(assets.UploadError) as exc:
            assets.store_upload(
                s, paths, original_name="doc.pdf", content=b"%PDF-1.4", mime="application/pdf"
            )
        assert exc.value.status == 415


def test_oversize_upload_raises_413(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        with pytest.raises(assets.UploadError) as exc:
            assets.store_upload(
                s,
                paths,
                original_name="huge.png",
                content=b"\0" * (2 * 1024 * 1024),
                mime="image/png",
                max_mb=1,
            )
        assert exc.value.status == 413


def test_normalize_and_list_tags():
    assert assets.normalize_tags("Fox, animals , fox") == ",fox,animals,"
    assert assets.normalize_tags("") == ","
    assert assets.tags_list(",fox,animals,") == ["fox", "animals"]
    assert assets.tags_list(",") == []


def test_list_assets_filters_kind_tag_q_and_pages(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        a1, _ = assets.store_upload(
            s,
            paths,
            original_name="fox-one.png",
            content=_png((8, 8)),
            mime="image/png",
            tags="fox, animals",
        )
        a2, _ = assets.store_upload(
            s,
            paths,
            original_name="fox-two.png",
            content=_png((16, 16)),
            mime="image/png",
            tags="fox",
        )
        a3, _ = assets.store_upload(
            s, paths, original_name="clip.mp4", content=_mp4(), mime="video/mp4", tags="animals"
        )
        assets.set_notes(s, a1.id, "a red fox in the snow")
        ids = (a1.id, a2.id, a3.id)

    with db.session_scope(f) as s:
        rows, total = assets.list_assets(s)
        assert total == 3
        assert [r.id for r in rows] == sorted(ids, reverse=True)

        rows, total = assets.list_assets(s, kind="video")
        assert total == 1 and rows[0].id == ids[2]

        rows, total = assets.list_assets(s, tag="animals")
        assert total == 2 and {r.id for r in rows} == {ids[0], ids[2]}

        rows, total = assets.list_assets(s, tag="fox")
        assert total == 2 and {r.id for r in rows} == {ids[0], ids[1]}

        rows, total = assets.list_assets(s, q="fox-two")
        assert total == 1 and rows[0].id == ids[1]

        rows, total = assets.list_assets(s, q="red fox")
        assert total == 1 and rows[0].id == ids[0]

        rows, total = assets.list_assets(s, page=1, per_page=2)
        assert len(rows) == 2
        rows, total = assets.list_assets(s, page=2, per_page=2)
        assert len(rows) == 1


def test_set_tags_and_notes(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id
    with db.session_scope(f) as s:
        updated = assets.set_tags(s, aid, "Cats, cats, dogs")
        assert updated.tags == ",cats,dogs,"
        updated = assets.set_notes(s, aid, "cute")
        assert updated.notes == "cute"
    with db.session_scope(f) as s:
        a = assets.get(s, aid)
        assert a.tags == ",cats,dogs," and a.notes == "cute"


def test_delete_removes_row_and_files(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id
        sha12 = asset.sha256[:12]

    upload_path = paths.uploads / f"{sha12}.png"
    thumb_path = paths.thumbs / f"asset-{sha12}.jpg"
    assert upload_path.exists() and thumb_path.exists()

    with db.session_scope(f) as s:
        assert assets.delete(s, paths, aid) is True

    assert not upload_path.exists()
    assert not thumb_path.exists()
    with db.session_scope(f) as s:
        assert assets.get(s, aid) is None


def test_delete_missing_returns_false(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        assert assets.delete(s, paths, 999999) is False


def test_delete_refused_when_referenced_by_active_job(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id
        pid = s.query(models.Project).filter_by(slug="default").one().id
        s.add(
            models.Job(
                id="j1",
                project_id=pid,
                kind="image",
                status=models.JobStatus.queued.value,
                model_air="m1",
                request_json={"seed_image_asset_id": aid},
            )
        )

    with db.session_scope(f) as s:
        assert assets.delete(s, paths, aid) is False
        assert assets.get(s, aid) is not None

    # once the job is no longer active, delete succeeds
    with db.session_scope(f) as s:
        job = s.get(models.Job, "j1")
        job.status = models.JobStatus.succeeded.value

    with db.session_scope(f) as s:
        assert assets.delete(s, paths, aid) is True


def test_delete_refused_for_reference_asset_ids_list(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id
        pid = s.query(models.Project).filter_by(slug="default").one().id
        s.add(
            models.Job(
                id="j2",
                project_id=pid,
                kind="image",
                status=models.JobStatus.running.value,
                model_air="m1",
                request_json={"reference_asset_ids": [aid]},
            )
        )

    with db.session_scope(f) as s:
        assert assets.delete(s, paths, aid) is False


def test_list_assets_escapes_like_wildcards_in_tag(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        pct, _ = assets.store_upload(
            s,
            paths,
            original_name="pct.png",
            content=_png((8, 8)),
            mime="image/png",
            tags="100%",
        )
        thousand, _ = assets.store_upload(
            s,
            paths,
            original_name="thousand.png",
            content=_png((9, 9)),
            mime="image/png",
            tags="1000",
        )
        pct_id = pct.id
        assert thousand.id != pct_id

    with db.session_scope(f) as s:
        rows, total = assets.list_assets(s, tag="100%")
        assert total == 1 and rows[0].id == pct_id


def test_list_assets_escapes_like_wildcards_in_q(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        underscore, _ = assets.store_upload(
            s, paths, original_name="a_b.png", content=_png((10, 10)), mime="image/png"
        )
        assets.store_upload(
            s, paths, original_name="axb.png", content=_png((11, 11)), mime="image/png"
        )
        underscore_id = underscore.id

    with db.session_scope(f) as s:
        rows, total = assets.list_assets(s, q="a_b")
        assert total == 1 and rows[0].id == underscore_id


def test_store_upload_tolerates_duplicate_insert_race(booted, monkeypatch):
    """A second process can commit the same sha256 between our lookup and our insert;
    the unique constraint should be recovered from, not raised."""
    paths, f = booted
    content = _png()
    with db.session_scope(f) as s:
        first, created = assets.store_upload(
            s, paths, original_name="first.png", content=content, mime="image/png"
        )
        assert created is True
        existing_id = first.id

    calls = {"n": 0}
    real_find = assets._find_by_sha256

    def flaky_find(session, digest):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # the race: our lookup misses the row another process just committed
        return real_find(session, digest)

    monkeypatch.setattr(assets, "_find_by_sha256", flaky_find)

    with db.session_scope(f) as s:
        asset, created = assets.store_upload(
            s, paths, original_name="second.png", content=content, mime="image/png"
        )
        assert created is False
        assert asset.id == existing_id
    with db.session_scope(f) as s:
        assert s.query(models.Asset).filter_by(sha256=first.sha256).count() == 1


async def test_ensure_media_uuid_uploads_once_then_caches(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id

    fake = FakeRunware({"media_storage": [[{"mediaUUID": "uuid-1", "mediaURL": "http://x/1"}]]})
    uuid1 = await assets.ensure_media_uuid(fake, f, paths, aid)
    assert uuid1 == "uuid-1"
    assert len(fake.calls) == 1

    uuid2 = await assets.ensure_media_uuid(fake, f, paths, aid)
    assert uuid2 == "uuid-1"
    assert len(fake.calls) == 1  # cached: no second upload

    with db.session_scope(f) as s:
        a = assets.get(s, aid)
        assert a.media_uuid == "uuid-1" and a.media_url == "http://x/1"
        assert a.media_uploaded_at is not None


async def test_ensure_media_uuid_reuploads_when_cache_expired(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id
        asset.media_uuid = "old-uuid"
        asset.media_url = "http://x/old"
        asset.media_uploaded_at = utcnow() - timedelta(days=7)

    fake = FakeRunware({"media_storage": [[{"mediaUUID": "uuid-2", "mediaURL": "http://x/2"}]]})
    uuid = await assets.ensure_media_uuid(fake, f, paths, aid)
    assert uuid == "uuid-2"
    assert len(fake.calls) == 1
    with db.session_scope(f) as s:
        a = assets.get(s, aid)
        assert a.media_uuid == "uuid-2" and a.media_url == "http://x/2"


async def test_ensure_media_uuid_raises_runware_error(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id

    fake = FakeRunware({"media_storage": [RunwareError("invalidApiKey", "bad key")]})
    with pytest.raises(RunwareError):
        await assets.ensure_media_uuid(fake, f, paths, aid)


async def test_media_map_skips_unknown_ids_and_returns_uuids(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id

    fake = FakeRunware({"media_storage": [[{"mediaUUID": "uuid-1", "mediaURL": "http://x/1"}]]})
    result = await assets.media_map(fake, f, paths, [aid, 999999])
    assert result == {aid: "uuid-1"}


async def test_media_map_wraps_upload_failure_with_asset_name(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="bad-ref.png", content=_png(), mime="image/png"
        )
        aid = asset.id

    fake = FakeRunware({"media_storage": [RunwareError("invalidApiKey", "bad key")]})
    with pytest.raises(assets.MediaUploadError) as exc:
        await assets.media_map(fake, f, paths, [aid])
    assert exc.value.original_name == "bad-ref.png"
    assert isinstance(exc.value.cause, RunwareError)


async def test_ensure_media_uuid_reply_missing_media_url_stores_none(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id

    fake = FakeRunware({"media_storage": [[{"mediaUUID": "u1"}]]})
    uuid = await assets.ensure_media_uuid(fake, f, paths, aid)
    assert uuid == "u1"
    with db.session_scope(f) as s:
        a = assets.get(s, aid)
        assert a.media_uuid == "u1" and a.media_url is None


async def test_ensure_media_uuid_reply_missing_media_uuid_raises(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id

    fake = FakeRunware({"media_storage": [[{}]]})
    with pytest.raises(assets.MediaUploadError) as exc:
        await assets.ensure_media_uuid(fake, f, paths, aid)
    assert "fox.png" in exc.value.message
    with db.session_scope(f) as s:
        a = assets.get(s, aid)
        assert a.media_uuid is None  # the failed attempt never wrote a cache entry


async def test_ensure_media_uuid_missing_file_raises_media_upload_error(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        aid = asset.id
        path = assets.abs_path(paths, asset)
    path.unlink()

    fake = FakeRunware({})
    with pytest.raises(assets.MediaUploadError) as exc:
        await assets.ensure_media_uuid(fake, f, paths, aid)
    assert "fox.png" in exc.value.message and "missing on disk" in exc.value.message
    assert fake.calls == []  # the file check runs before any RunWare call


def test_abs_path_and_public_urls(booted):
    paths, f = booted
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="fox.png", content=_png(), mime="image/png"
        )
        sha12 = asset.sha256[:12]
        resolved = assets.abs_path(paths, asset)
        assert resolved == (paths.uploads / asset.filename).resolve()
        assert assets.thumb_rel(asset) is not None
        urls = assets.public_urls(asset)
        assert urls["url"] == f"/files/uploads/{asset.filename}"
        assert urls["thumb_url"] == f"/files/asset-thumbs/asset-{sha12}.jpg"
