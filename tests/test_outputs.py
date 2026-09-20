import json

import pytest
from PIL import Image

import vjhstudio
from vjhstudio import boot, config, db, models
from vjhstudio.runware.download import SavedFile
from vjhstudio.runware.results import ResultItem
from vjhstudio.services import outputs
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
