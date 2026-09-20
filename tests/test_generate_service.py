import pytest

from vjhstudio import boot, config, db, models
from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.services import generate


@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    return paths, info.session_factory


def test_enqueue_creates_queued_job(env):
    paths, f = env
    with db.session_scope(f) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
    req = ImageRequest(
        project_id=pid, model="runware:101@1", form=PromptForm(subject="a fox", negative="blurry")
    )
    job = generate.enqueue_image(f, paths, req, default_negative="low quality")
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert (
            j.status == "queued"
            and j.kind == "image"
            and j.title == "a fox"
            and j.expected_ms == 2942
        )
        assert j.request_json["negative"].startswith("blurry, low quality")


def test_enqueue_rejects_non_image_model_and_bad_project(env):
    paths, f = env
    with pytest.raises(ValueError, match="not an image model"):
        generate.enqueue_image(
            f,
            paths,
            ImageRequest(project_id=1, model="google:3@2", form=PromptForm(subject="x")),
            default_negative="",
        )
    with pytest.raises(ValueError, match="project"):
        generate.enqueue_image(
            f,
            paths,
            ImageRequest(project_id=999, model="runware:101@1", form=PromptForm(subject="x")),
            default_negative="",
        )
    with pytest.raises(ValueError, match="not an image model"):
        generate.enqueue_image(
            f,
            paths,
            ImageRequest(project_id=1, model="nobody:0@0", form=PromptForm(subject="x")),
            default_negative="",
        )


def test_enqueue_title_falls_back_and_uses_observed_latency(env):
    paths, f = env
    with db.session_scope(f) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
        from vjhstudio.services import costs

        costs.observe_latency(s, "runware:101@1", 8000)
    job = generate.enqueue_image(
        f,
        paths,
        ImageRequest(project_id=pid, model="runware:101@1", form=PromptForm()),
        default_negative="",
    )
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.title == "Untitled" and j.expected_ms == 8000
