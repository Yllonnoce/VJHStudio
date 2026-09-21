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


def _req(pid: int, **extra) -> ImageRequest:
    return ImageRequest(
        project_id=pid, model="runware:101@1", form=PromptForm(subject="a fox", **extra)
    )


def test_enqueue_records_prompt_history_and_links_the_job(env):
    paths, f = env
    with db.session_scope(f) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
    job = generate.enqueue_image(f, paths, _req(pid), default_negative="low quality")
    with db.session_scope(f) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.kind == "image" and prompt.use_count == 1 and prompt.project_id == pid
        assert prompt.composed_prompt == "a fox"
        assert prompt.negative_prompt.startswith("low quality")
        assert s.get(models.Job, job.id).prompt_id == prompt.id


def test_enqueue_dedupes_identical_text_and_keeps_the_polish_blob(env):
    paths, f = env
    with db.session_scope(f) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
    blob = {"source": "promptEnhance", "versions": ["a fox, refined"], "chosen_index": 0}
    j1 = generate.enqueue_image(f, paths, _req(pid), default_negative="", polish_json=blob)
    j2 = generate.enqueue_image(f, paths, _req(pid), default_negative="")
    with db.session_scope(f) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.use_count == 2 and prompt.polish_json == blob
        assert s.get(models.Job, j1.id).prompt_id == prompt.id
        assert s.get(models.Job, j2.id).prompt_id == prompt.id


def test_enqueue_video_links_a_video_prompt_row(env):
    from vjhstudio.schemas.video import VideoRequest

    paths, f = env
    with db.session_scope(f) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
    req = VideoRequest(project_id=pid, model="google:3@2", form=PromptForm(subject="a fox runs"))
    job = generate.enqueue_video(f, paths, req)
    with db.session_scope(f) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.kind == "video" and prompt.negative_prompt == ""
        assert s.get(models.Job, job.id).prompt_id == prompt.id
