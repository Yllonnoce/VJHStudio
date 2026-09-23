import json
from pathlib import Path

import pytest
from PIL import Image

import vjhstudio
from vjhstudio import boot, config, db, models
from vjhstudio.runware.download import SavedFile
from vjhstudio.runware.results import ResultItem
from vjhstudio.services import costs, outputs
from vjhstudio.services import projects as projects_svc
from vjhstudio.services import settings as settings_svc


def _saved(path, seed=7, cost=0.02):
    item = ResultItem(url="http://x/1.png", seed=seed, cost=cost, uuid=None, nsfw=False, raw={})
    return SavedFile(path=path, size=path.stat().st_size, url=item.url, item=item)


def _meta(pid):
    return outputs.OutputMeta(
        job_id="j1",
        project_id=pid,
        project_slug="default",
        kind="image",
        model_air="m1",
        prompt_text="fox",
        negative_prompt="blurry",
    )


def _png_path(paths, row):
    """The file behind an ``env`` row, as an absolute path."""
    return paths.outputs / row.rel_path


def _png(paths, name="20260920-130000-bbbbbb.png"):
    path = paths.outputs / "default" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (9, 9, 9)).save(path)
    return path


@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    with db.session_scope(info.session_factory) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
        s.add(
            models.Job(
                id="j1",
                project_id=pid,
                kind="image",
                status="succeeded",
                model_air="m1",
                request_json={
                    "model": "m1",
                    "form": {"subject": "fox"},
                    "width": 512,
                    "height": 512,
                },
            )
        )
        for i in range(3):
            p = paths.outputs / "default" / f"20260920-12000{i}-aaaaaa.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
            s.add(
                models.Output(
                    job_id="j1",
                    project_id=pid,
                    kind="image",
                    filename=p.name,
                    rel_path=f"default/{p.name}",
                    sidecar_rel_path=f"default/{p.stem}.json",
                    model_air="m1",
                    prompt_text=f"fox {i}",
                    params_json={},
                    seed=i,
                    is_favourite=(i == 1),
                )
            )
    return paths, info.session_factory, pid


def test_gallery_filters_and_paging(env):
    paths, f, pid = env
    with db.session_scope(f) as s:
        rows, total = outputs.gallery(s, project_id=pid)
        assert total == 3 and [o.seed for o in rows] == [2, 1, 0]
        assert outputs.gallery(s, favourite=True)[1] == 1
        assert outputs.gallery(s, q="fox 2")[1] == 1
        assert outputs.gallery(s, model="nope")[1] == 0
        assert outputs.gallery(s, model="m1")[1] == 3
        assert outputs.gallery(s, kind="video")[1] == 0
        assert len(outputs.gallery(s, page=2, per_page=2)[0]) == 1
        assert outputs.gallery(s, date_from="2026-09-19")[1] == 3
        assert outputs.gallery(s, date_to="2020-01-01")[1] == 0


def test_favourite_delete_remix(env):
    paths, f, pid = env
    with db.session_scope(f) as s:
        o = outputs.gallery(s)[0][0]
        assert outputs.toggle_favourite(s, o.id).is_favourite is True
        req = outputs.remix_request(o)
        assert req["seed"] == o.seed and req["form"]["subject"] == "fox"
        path = outputs.abs_path(paths, o)
        assert outputs.delete(s, paths, o.id) is True and not path.exists()
        assert outputs.gallery(s)[1] == 2
        assert outputs.delete(s, paths, 9999) is False


def test_mark_missing_flags_deleted_files(env):
    paths, f, pid = env
    with db.session_scope(f) as s:
        rows, _ = outputs.gallery(s)
        outputs.abs_path(paths, rows[0]).unlink()
        assert outputs.mark_missing(s, paths) == 1
        assert s.get(models.Output, rows[0].id).is_missing is True
        assert outputs.mark_missing(s, paths) == 0


def test_prepare_does_the_file_work_outside_any_transaction(env):
    """Sidecars and JPEG encoding happen before a session is opened; insert() is the short bit."""
    paths, f, pid = env
    png = _png(paths)
    prepared = outputs.prepare(
        paths,
        _meta(pid),
        [_saved(png)],
        {"width": 8},
        {"task_sent": {"model": "m1"}},
        root=paths.outputs,
    )
    p0 = prepared[0]
    assert (
        p0.thumb_rel_path == f"thumbs/{png.stem}.jpg" and (paths.data / p0.thumb_rel_path).exists()
    )
    assert p0.rel_path == f"default/{png.name}" and (paths.outputs / p0.sidecar_rel_path).exists()
    side = json.loads((paths.outputs / p0.sidecar_rel_path).read_text())
    assert side["app_version"] == vjhstudio.__version__ and side["project"] == "default"
    assert side["negative_prompt"] == "blurry" and "negative" not in side
    assert side["job_id"] == "j1" and side["seed"] == 7 and side["cost"] == 0.02
    with db.session_scope(f) as s:
        assert s.query(models.Output).count() == 3  # prepare() created no rows of its own
        rows = outputs.insert(s, _meta(pid), prepared)
        assert len(rows) == 1 and rows[0].width == 8 and rows[0].params_json == {"width": 8}
        assert rows[0].thumb_rel_path == p0.thumb_rel_path


def test_record_outputs_honours_an_explicit_root(env, tmp_path):
    """The caller's root wins over the setting, so a mid-job change cannot break relative_to."""
    paths, f, pid = env
    png = _png(paths, "20260920-140000-cccccc.png")
    with db.session_scope(f) as s:
        settings_svc.set_many(s, {"paths.outputs_dir": str(tmp_path / "elsewhere")})
        job = s.get(models.Job, "j1")
        rows = outputs.record_outputs(
            s, paths, job, [_saved(png)], {"width": 8}, root=paths.outputs
        )
        assert rows[0].rel_path == f"default/{png.name}"


def test_paths_outside_the_outputs_root_are_refused(env):
    """A row whose rel_path escapes the root must 404, never be followed onto the disk."""
    paths, f, pid = env
    assert paths.db.exists()
    with db.session_scope(f) as s:
        o = outputs.gallery(s)[0][0]
        o.rel_path = "../vjh.db"
        s.flush()
        with pytest.raises(LookupError):
            outputs.abs_path(paths, o)
        assert outputs.mark_missing(s, paths) == 1
        assert s.get(models.Output, o.id).is_missing is True
        assert outputs.delete(s, paths, o.id) is True
    assert paths.db.exists()


def test_mark_missing_clears_the_flag_when_the_file_is_back(env):
    """A restore puts files back under rows flagged long ago; the sweep must unflag them."""
    paths, f, pid = env
    with db.session_scope(f) as s:
        row = outputs.gallery(s)[0][0]
        path = outputs.abs_path(paths, row)
        content = path.read_bytes()
        path.unlink()
        assert outputs.mark_missing(s, paths) == 1
        assert s.get(models.Output, row.id).is_missing is True
        path.write_bytes(content)
        assert outputs.mark_missing(s, paths) == 0
        assert s.get(models.Output, row.id).is_missing is False


# ---- video posters -------------------------------------------------------
def _video_meta(pid):
    return outputs.OutputMeta(
        job_id="j1",
        project_id=pid,
        project_slug="default",
        kind="video",
        model_air="m1",
        prompt_text="a fox running",
        negative_prompt="",
    )


def _video_row(paths, f, pid, name, *, on_disk=True, make_mp4=None):
    path = paths.outputs / "default" / name
    if on_disk:
        make_mp4(path)
    with db.session_scope(f) as s:
        row = models.Output(
            job_id="j1",
            project_id=pid,
            kind="video",
            filename=name,
            rel_path=f"default/{name}",
            sidecar_rel_path=f"default/{Path(name).stem}.json",
            model_air="m1",
            prompt_text="a fox running",
            params_json={},
        )
        s.add(row)
        s.flush()
        return row.id


def test_prepare_gives_a_video_a_poster_that_doubles_as_its_thumbnail(env, make_mp4):
    """The frame is stored once and pointed at twice, so every "thumb" consumer that
    predates posters keeps working without knowing a video is involved."""
    paths, f, pid = env
    clip = make_mp4(paths.outputs / "default" / "20260920-140000-cccccc.mp4", size="640x360")
    meta = _video_meta(pid)
    prepared = outputs.prepare(
        paths,
        meta,
        [_saved(clip)],
        {"width": 640, "height": 360, "duration": 5},
        root=paths.outputs,
        dims=(640, 360),
        duration_s=5.0,
        thumbnail=False,
        poster=True,
    )
    p0 = prepared[0]
    rel = f"thumbs/{clip.stem}.jpg"
    assert p0.poster_rel_path == rel and p0.thumb_rel_path == rel
    with Image.open(paths.data / rel) as im:
        assert im.format == "JPEG" and max(im.size) <= 384
    with db.session_scope(f) as s:
        row = outputs.insert(s, meta, prepared)[0]
        assert row.poster_rel_path == rel and row.thumb_rel_path == rel
        assert row.kind == "video" and row.duration_s == 5.0


def test_prepare_leaves_an_image_alone(env):
    """poster=True is never passed for an image, but it must not change one either."""
    paths, f, pid = env
    png = _png(paths, "20260920-150000-dddddd.png")
    p0 = outputs.prepare(paths, _meta(pid), [_saved(png)], {}, root=paths.outputs, poster=True)[0]
    assert p0.thumb_rel_path == f"thumbs/{png.stem}.jpg" and p0.poster_rel_path is None


def test_backfill_posters_fills_a_missing_poster_and_skips_a_gone_file(env, make_mp4):
    paths, f, pid = env
    here = _video_row(paths, f, pid, "20260920-160000-eeeeee.mp4", make_mp4=make_mp4)
    gone = _video_row(paths, f, pid, "20260920-160001-ffffff.mp4", on_disk=False)
    assert outputs.backfill_posters(f, paths) == 1
    with db.session_scope(f) as s:
        filled = s.get(models.Output, here)
        rel = "thumbs/20260920-160000-eeeeee.jpg"
        assert filled.poster_rel_path == rel and filled.thumb_rel_path == rel
        assert (paths.data / rel).is_file()
        assert s.get(models.Output, gone).poster_rel_path is None
    # a second pass has nothing left to do: rows with a poster are never revisited
    assert outputs.backfill_posters(f, paths) == 0


def test_backfill_posters_honours_a_limit(env, make_mp4):
    paths, f, pid = env
    for i in range(2):
        _video_row(paths, f, pid, f"20260920-17000{i}-aaaaab.mp4", make_mp4=make_mp4)
    assert outputs.backfill_posters(f, paths, limit=1) == 1
    assert outputs.backfill_posters(f, paths) == 1


def test_backfill_posters_stops_when_asked_to(env, make_mp4):
    """Shutdown cannot interrupt the worker thread, so the loop has to check between
    rows -- and stop before it would open another session on a disposed engine."""
    paths, f, pid = env
    for i in range(3):
        _video_row(paths, f, pid, f"20260920-19000{i}-aaaaac.mp4", make_mp4=make_mp4)
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # let exactly the first row through

    assert outputs.backfill_posters(f, paths, should_stop=should_stop) == 1
    with db.session_scope(f) as s:
        filled = [o for o in s.query(models.Output).all() if o.poster_rel_path]
        assert len(filled) == 1
    # nothing was consumed: the two it skipped are still waiting for the next run
    assert outputs.backfill_posters(f, paths) == 2


def test_delete_removes_a_videos_poster_file(env, make_mp4):
    paths, f, pid = env
    oid = _video_row(paths, f, pid, "20260920-200000-aaaaad.mp4", make_mp4=make_mp4)
    assert outputs.backfill_posters(f, paths) == 1
    poster = paths.thumbs / "20260920-200000-aaaaad.jpg"
    assert poster.is_file()
    with db.session_scope(f) as s:
        assert outputs.delete(s, paths, oid) is True
    assert not poster.exists()


# ---- moving an output to another project ---------------------------------
def _sidecar_for(paths, row, project="default"):
    side = paths.outputs / row.sidecar_rel_path
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps({"project": project, "job_id": row.job_id}), encoding="utf-8")
    return side


def _solo_output(s, paths, pid, name="20260921-090000-dddddd.png", job_id="jsolo"):
    """An output that is the only one its job produced, with a real file on disk.

    The ``env`` rows all share job j1, and a job's cost only follows once its *last*
    output has moved -- so a test about the cost following needs a job of its own."""
    s.add(
        models.Job(
            id=job_id,
            project_id=pid,
            kind="image",
            status="succeeded",
            model_air="m1",
            request_json={"model": "m1"},
        )
    )
    path = paths.outputs / "default" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"solo")
    row = models.Output(
        job_id=job_id,
        project_id=pid,
        kind="image",
        filename=name,
        rel_path=f"default/{name}",
        sidecar_rel_path=f"default/{Path(name).stem}.json",
        model_air="m1",
        prompt_text="a solo fox",
        params_json={},
        seed=9,
    )
    s.add(row)
    s.flush()
    return row


def test_move_takes_the_file_its_sidecar_and_its_cost_along(env):
    paths, f, pid = env
    with db.session_scope(f) as s:
        other = projects_svc.create(s, paths, "Book covers")
        row = _solo_output(s, paths, pid)
        old_media, old_side = _png_path(paths, row), _sidecar_for(paths, row)
        row.thumb_rel_path = "thumbs/keepme.jpg"
        costs.record_usage(
            s, job=s.get(models.Job, "jsolo"), task_type="imageInference", cost=0.5, model_air="m1"
        )
        s.flush()

        moved = outputs.move(s, paths, row.id, other.id)

        assert moved.project_id == other.id
        assert moved.rel_path == f"{other.slug}/{moved.filename}"
        assert moved.sidecar_rel_path == f"{other.slug}/{Path(moved.filename).stem}.json"
        assert (paths.outputs / moved.rel_path).is_file() and not old_media.exists()
        side = paths.outputs / moved.sidecar_rel_path
        assert side.is_file() and not old_side.exists()
        # the sidecar's own "project" field follows the file it describes
        assert json.loads(side.read_text())["project"] == other.slug
        # thumbs are shared and project-independent: the path is untouched
        assert moved.thumb_rel_path == "thumbs/keepme.jpg"
        # the cost follows, via the job and its usage rows: this job made nothing else
        assert s.get(models.Job, "jsolo").project_id == other.id
        totals = costs.totals_by_project(s)
        assert totals[other.id] == {"outputs": 1, "cost": 0.5}
        assert totals[pid]["outputs"] == 3 and totals[pid]["cost"] == 0.0


def test_move_suffixes_a_name_that_is_already_taken(env):
    paths, f, pid = env
    with db.session_scope(f) as s:
        other = projects_svc.create(s, paths, "Book covers")
        row = outputs.gallery(s)[0][0]
        name = row.filename
        _png_path(paths, row).write_bytes(b"mine")
        clash = paths.outputs / other.slug / name
        clash.parent.mkdir(parents=True, exist_ok=True)
        clash.write_bytes(b"theirs")

        moved = outputs.move(s, paths, row.id, other.id)

        assert moved.filename == f"{Path(name).stem}-2.png"
        assert clash.read_bytes() == b"theirs"  # the stranger's file is never overwritten
        assert (paths.outputs / moved.rel_path).read_bytes() == b"mine"


def test_move_of_a_missing_file_still_takes_the_sidecar(env):
    """The media file can be gone while its sidecar -- the only record of how it was
    made -- is still there. That must travel with the row, not be orphaned."""
    paths, f, pid = env
    with db.session_scope(f) as s:
        other = projects_svc.create(s, paths, "Book covers")
        row = outputs.gallery(s)[0][0]
        old_side = _sidecar_for(paths, row)
        _png_path(paths, row).unlink()

        moved = outputs.move(s, paths, row.id, other.id)

        assert moved.project_id == other.id and moved.is_missing is True
        assert moved.rel_path == f"{other.slug}/{moved.filename}"
        assert not (paths.outputs / moved.rel_path).exists()  # no media file was invented
        side = paths.outputs / moved.sidecar_rel_path
        assert side.is_file() and not old_side.exists()
        assert json.loads(side.read_text())["project"] == other.slug


def test_move_to_the_same_project_changes_nothing(env):
    paths, f, pid = env
    with db.session_scope(f) as s:
        row = outputs.gallery(s)[0][0]
        before = (row.rel_path, row.sidecar_rel_path, row.filename)
        moved = outputs.move(s, paths, row.id, pid)
        assert (moved.rel_path, moved.sidecar_rel_path, moved.filename) == before
        assert moved.project_id == pid


def test_move_refuses_an_unknown_or_archived_project(env):
    paths, f, pid = env
    with db.session_scope(f) as s:
        row = outputs.gallery(s)[0][0]
        with pytest.raises(outputs.OutputMoveError):
            outputs.move(s, paths, row.id, 999999)
        shelved = projects_svc.create(s, paths, "Shelved")
        projects_svc.set_archived(s, shelved.id, True)
        with pytest.raises(outputs.OutputMoveError):
            outputs.move(s, paths, row.id, shelved.id)
        assert s.get(models.Output, row.id).project_id == pid
        with pytest.raises(LookupError):
            outputs.move(s, paths, 999999, pid)


def test_move_never_follows_a_path_out_of_the_outputs_root(env):
    """A crafted rel_path must not let a move drag the database into a project folder."""
    paths, f, pid = env
    assert paths.db.exists()
    with db.session_scope(f) as s:
        other = projects_svc.create(s, paths, "Book covers")
        row = outputs.gallery(s)[0][0]
        row.rel_path = "../vjh.db"
        s.flush()
        with pytest.raises(outputs.OutputMoveError):
            outputs.move(s, paths, row.id, other.id)
        assert s.get(models.Output, row.id).project_id == pid
        assert s.get(models.Output, row.id).rel_path == "../vjh.db"
    assert paths.db.exists()


def test_a_jobs_cost_follows_only_once_its_last_output_has(env):
    """A task is billed once for everything it returned, so the spend cannot be split
    over the images. It therefore stays put until the whole job has moved."""
    paths, f, pid = env
    with db.session_scope(f) as s:
        other = projects_svc.create(s, paths, "Book covers")
        costs.record_usage(
            s, job=s.get(models.Job, "j1"), task_type="imageInference", cost=0.6, model_air="m1"
        )
        rows = outputs.gallery(s)[0]  # all three came out of job j1
        s.flush()

        for row in rows[:-1]:
            outputs.move(s, paths, row.id, other.id)
            # a sibling is still in the old project: the job and its spend stay behind
            assert s.get(models.Job, "j1").project_id == pid
            assert costs.totals_by_project(s)[pid]["cost"] == 0.6
            assert costs.totals_by_project(s)[other.id]["cost"] == 0.0

        outputs.move(s, paths, rows[-1].id, other.id)
        assert s.get(models.Job, "j1").project_id == other.id
        totals = costs.totals_by_project(s)
        assert totals[other.id] == {"outputs": 3, "cost": 0.6}
        assert totals.get(pid, {"outputs": 0, "cost": 0.0})["cost"] == 0.0


def test_a_failed_row_update_puts_the_files_back(env, monkeypatch):
    """The files move first. If the row that describes them then cannot be written, the
    files come home again -- and the session is rolled back, so a caller that catches the
    error cannot commit half a move."""
    paths, f, pid = env
    with db.session_scope(f) as s:
        other = projects_svc.create(s, paths, "Book covers")
        row = _solo_output(s, paths, pid)
        media, side = _png_path(paths, row), _sidecar_for(paths, row)
        s.commit()  # the rollback inside move() must not take the fixture with it

        real_flush = s.flush

        def boom(*args, **kwargs):
            """Fail exactly the flush that would write the moved row."""
            if any(isinstance(x, models.Output) for x in s.dirty):
                raise RuntimeError("the database went away")
            return real_flush(*args, **kwargs)

        monkeypatch.setattr(s, "flush", boom)
        with pytest.raises(outputs.OutputMoveError, match="could not record the move"):
            outputs.move(s, paths, row.id, other.id)
        monkeypatch.undo()

        assert media.is_file() and side.is_file()
        assert not (paths.outputs / other.slug / media.name).exists()
        assert not (paths.outputs / other.slug / side.name).exists()
        again = s.get(models.Output, row.id)
        assert again.project_id == pid and again.rel_path == f"default/{media.name}"
        assert again.filename == media.name and again.sidecar_rel_path == f"default/{side.name}"
        assert s.get(models.Job, "jsolo").project_id == pid
