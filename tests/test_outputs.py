import pytest

from vjhstudio import boot, config, db, models
from vjhstudio.services import outputs


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
