from vjhstudio import boot, config, db, models
from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.schemas.video import VideoRequest
from vjhstudio.services import projects, prompts


def _env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    return paths, info.session_factory


def _project_id(f):
    with db.session_scope(f) as s:
        return s.query(models.Project).filter_by(slug="default").one().id


def test_hash_of_matches_the_stored_content_hash(tmp_path):
    """Phase 6 merge note: an archive merge must recompute the hash from a saved row's
    own stored columns, never from live settings (e.g. the local default negative
    prompt), so ``hash_of`` reads ``form_json``/``composed_prompt``/``final_prompt``/
    ``negative_prompt`` off the row itself and must agree with what ``upsert`` wrote."""
    _, f = _env(tmp_path)
    pid = _project_id(f)
    form = PromptForm(subject="a fox")
    with db.session_scope(f) as s:
        p, created = prompts.upsert(
            s,
            project_id=pid,
            kind="image",
            title="Fox",
            form=form,
            final_prompt="a fox, in a forest",
            negative_prompt="blurry",
        )
        assert created is True
        assert prompts.hash_of(p) == p.content_hash


def test_find_by_hash_is_public_and_returns_lowest_id_on_a_duplicate(tmp_path):
    _, f = _env(tmp_path)
    pid = _project_id(f)
    form = PromptForm(subject="a fox")
    with db.session_scope(f) as s:
        original, _ = prompts.upsert(
            s, project_id=pid, kind="image", title="Fox", form=form, final_prompt="a fox"
        )
        original_id = original.id
        copy = prompts.duplicate(s, original_id)
        assert copy.content_hash == original.content_hash
    with db.session_scope(f) as s:
        found = prompts.find_by_hash(s, pid, original.content_hash)
        assert found.id == original_id


def test_upsert_same_content_dedupes_within_a_project(tmp_path):
    _, f = _env(tmp_path)
    form = PromptForm(subject="a fox")
    pid = _project_id(f)
    with db.session_scope(f) as s:
        p1, created1 = prompts.upsert(
            s,
            project_id=pid,
            kind="image",
            title="Fox",
            form=form,
            final_prompt="a fox, in a forest",
        )
        assert created1 is True
        id1 = p1.id
    with db.session_scope(f) as s:
        p2, created2 = prompts.upsert(
            s,
            project_id=pid,
            kind="image",
            title="Fox again",
            form=form,
            final_prompt="a fox, in a forest",
        )
        assert created2 is False
        assert p2.id == id1
    with db.session_scope(f) as s:
        assert s.query(models.Prompt).count() == 1


def test_upsert_different_project_creates_a_second_row(tmp_path):
    paths, f = _env(tmp_path)
    form = PromptForm(subject="a fox")
    pid = _project_id(f)
    with db.session_scope(f) as s:
        other = projects.create(s, paths, "Other")
        other_id = other.id
    with db.session_scope(f) as s:
        prompts.upsert(
            s, project_id=pid, kind="image", title="Fox", form=form, final_prompt="a fox"
        )
        prompts.upsert(
            s, project_id=other_id, kind="image", title="Fox", form=form, final_prompt="a fox"
        )
    with db.session_scope(f) as s:
        assert s.query(models.Prompt).count() == 2


def test_upsert_merges_tags_and_keeps_polish_json_when_none_given(tmp_path):
    _, f = _env(tmp_path)
    form = PromptForm(subject="a fox")
    pid = _project_id(f)
    with db.session_scope(f) as s:
        p1, _ = prompts.upsert(
            s,
            project_id=pid,
            kind="image",
            title="Fox",
            form=form,
            final_prompt="a fox",
            tags="animals",
            polish_json={"versions": ["a"]},
        )
        id1 = p1.id
    with db.session_scope(f) as s:
        p2, created = prompts.upsert(
            s,
            project_id=pid,
            kind="image",
            title="Fox",
            form=form,
            final_prompt="a fox",
            tags="forest",
        )
        assert created is False
        assert p2.id == id1
        assert set(prompts.tags_list(p2.tags)) == {"animals", "forest"}
        assert p2.polish_json == {"versions": ["a"]}


def test_for_request_creates_then_reuses_and_bumps_use_count(tmp_path):
    _, f = _env(tmp_path)
    pid = _project_id(f)
    req = ImageRequest(project_id=pid, model="runware:101@1", form=PromptForm(subject="a fox"))
    with db.session_scope(f) as s:
        p1 = prompts.for_request(s, req, kind="image")
        assert p1.use_count == 1
        id1 = p1.id
    with db.session_scope(f) as s:
        p2 = prompts.for_request(s, req, kind="image")
        assert p2.id == id1
        assert p2.use_count == 2
    with db.session_scope(f) as s:
        assert s.query(models.Prompt).count() == 1


def test_for_request_with_stale_prompt_id_creates_a_new_row(tmp_path):
    _, f = _env(tmp_path)
    pid = _project_id(f)
    req = ImageRequest(project_id=pid, model="runware:101@1", form=PromptForm(subject="a fox"))
    with db.session_scope(f) as s:
        original = prompts.for_request(s, req, kind="image")
        original_id = original.id
        assert original.use_count == 1

    edited_req = ImageRequest(
        project_id=pid,
        prompt_id=original_id,
        model="runware:101@1",
        form=PromptForm(subject="a fox"),
        final_prompt="a fox, edited text",
    )
    with db.session_scope(f) as s:
        new_prompt = prompts.for_request(s, edited_req, kind="image")
        assert new_prompt.id != original_id
        assert new_prompt.use_count == 1
    with db.session_scope(f) as s:
        old = prompts.get(s, original_id)
        assert old.use_count == 1
        assert s.query(models.Prompt).count() == 2


def test_for_request_ignores_a_stale_prompt_id_from_another_project(tmp_path):
    """A prompt_id can outlive a project switch in the UI (loaded on a prompt from
    project A, then the project select is changed to B before submit). Even when the
    content hash still matches, reusing that row would bump A's use_count and link B's
    job to A's prompt instead of creating B's own row -- I1."""
    paths, f = _env(tmp_path)
    pid_a = _project_id(f)
    with db.session_scope(f) as s:
        other = projects.create(s, paths, "Other")
        pid_b = other.id

    req_a = ImageRequest(project_id=pid_a, model="runware:101@1", form=PromptForm(subject="a fox"))
    with db.session_scope(f) as s:
        original = prompts.for_request(s, req_a, kind="image")
        original_id = original.id
        assert original.use_count == 1

    req_b = ImageRequest(
        project_id=pid_b,
        prompt_id=original_id,
        model="runware:101@1",
        form=PromptForm(subject="a fox"),
    )
    with db.session_scope(f) as s:
        new_prompt = prompts.for_request(s, req_b, kind="image")
        assert new_prompt.id != original_id
        assert new_prompt.project_id == pid_b
        assert new_prompt.use_count == 1

    with db.session_scope(f) as s:
        old = prompts.get(s, original_id)
        assert old.project_id == pid_a
        assert old.use_count == 1  # A's row must not be bumped by B's submit
        assert s.query(models.Prompt).count() == 2


def test_list_prompts_filters_and_paginates(tmp_path):
    _, f = _env(tmp_path)
    pid = _project_id(f)
    with db.session_scope(f) as s:
        for i in range(3):
            prompts.upsert(
                s,
                project_id=pid,
                kind="image",
                title=f"Fox {i}",
                form=PromptForm(subject=f"fox {i}"),
                final_prompt=f"fox {i}",
                tags="animal",
            )
        vid, _ = prompts.upsert(
            s,
            project_id=pid,
            kind="video",
            title="Cat video",
            form=PromptForm(subject="a cat"),
            final_prompt="a cat",
            tags="pet",
        )
        prompts.set_favourite(s, vid.id, True)

    with db.session_scope(f) as s:
        rows, total = prompts.list_prompts(s, kind="image")
        assert total == 3 and all(p.kind == "image" for p in rows)

        rows, total = prompts.list_prompts(s, favourite=True)
        assert total == 1 and rows[0].kind == "video"

        rows, total = prompts.list_prompts(s, project_id=pid)
        assert total == 4

        rows, total = prompts.list_prompts(s, tag="pet")
        assert total == 1 and rows[0].title == "Cat video"

        rows, total = prompts.list_prompts(s, q="cat")
        assert total == 1

        rows, total = prompts.list_prompts(s, per_page=2, page=1)
        assert len(rows) == 2 and total == 4
        rows2, total2 = prompts.list_prompts(s, per_page=2, page=2)
        assert len(rows2) == 2 and total2 == 4
        # newest first, no overlap between pages
        assert {r.id for r in rows} & {r.id for r in rows2} == set()


def test_duplicate_bypasses_dedupe_but_upsert_still_returns_the_lowest_id(tmp_path):
    _, f = _env(tmp_path)
    pid = _project_id(f)
    form = PromptForm(subject="a fox")
    with db.session_scope(f) as s:
        original, _ = prompts.upsert(
            s, project_id=pid, kind="image", title="Fox", form=form, final_prompt="a fox"
        )
        original_id = original.id

    with db.session_scope(f) as s:
        dup = prompts.duplicate(s, original_id)
        dup_id = dup.id
        assert dup_id != original_id
        assert dup.content_hash == prompts.get(s, original_id).content_hash
        assert dup.title == "Fox (copy)"
        assert dup.use_count == 0 and dup.last_used_at is None and dup.is_favourite is False

    with db.session_scope(f) as s:
        rows, total = prompts.list_prompts(s, q="fox")
        assert total == 2
        assert {r.id for r in rows} == {original_id, dup_id}

    with db.session_scope(f) as s:
        again, created = prompts.upsert(
            s, project_id=pid, kind="image", title="Fox", form=form, final_prompt="a fox"
        )
        assert created is False
        assert again.id == original_id


def test_delete_returns_true_then_false_and_job_survives_with_null_prompt_id(tmp_path):
    paths, f = _env(tmp_path)
    pid = _project_id(f)
    with db.session_scope(f) as s:
        p, _ = prompts.upsert(
            s,
            project_id=pid,
            kind="image",
            title="Fox",
            form=PromptForm(subject="a fox"),
            final_prompt="a fox",
        )
        prompt_id = p.id
        job = models.Job(
            id="job-1",
            project_id=pid,
            prompt_id=prompt_id,
            kind="image",
            model_air="runware:101@1",
            request_json={},
        )
        s.add(job)

    with db.session_scope(f) as s:
        assert prompts.delete(s, prompt_id) is True
    with db.session_scope(f) as s:
        assert prompts.delete(s, prompt_id) is False
    with db.session_scope(f) as s:
        job = s.get(models.Job, "job-1")
        assert job is not None
        assert job.prompt_id is None


def test_to_initial_round_trips_a_video_prompt(tmp_path):
    _, f = _env(tmp_path)
    pid = _project_id(f)
    req = VideoRequest(project_id=pid, model="runware:vid@1", form=PromptForm(subject="a cat"))
    with db.session_scope(f) as s:
        p = prompts.for_request(s, req, kind="video")
        prompt_id = p.id

    with db.session_scope(f) as s:
        p = prompts.get(s, prompt_id)
        initial = prompts.to_initial(p)
        assert initial["mode"] == "video"
        assert initial["prompt_id"] == prompt_id
        assert initial["project_id"] == pid
        assert set(["form", "final_prompt", "prompt_id", "project_id", "title", "mode"]) <= set(
            initial.keys()
        )


def test_saved_texts_matches_what_a_request_would_hash():
    """The save route and the submit path must derive (composed, final, negative) from
    one implementation, or the same prompt hashes twice and dedupe silently fails."""
    form = PromptForm(subject="a fox", style="oil painting", no_text=True)
    composed, final, negative = prompts.saved_texts("image", form, "a fox, oil painting", "blurry")
    assert composed == "a fox, oil painting"
    assert final == "a fox, oil painting" + prompts.NO_TEXT_SUFFIX  # typed finals are capped too
    assert negative.startswith("blurry") and "watermark" in negative

    req = ImageRequest(
        project_id=1, model="runware:101@1", form=form, final_prompt="  a fox, oil painting  "
    )
    assert prompts.final_prompt(req) == final

    # video carries no negative at all: the request omits negativePrompt
    _, _, video_negative = prompts.saved_texts("video", form, "", "blurry")
    assert video_negative == ""


def test_parse_polish_json_takes_objects_only_and_caps_the_size():
    assert prompts.parse_polish_json('{"a": 1}') == {"a": 1}
    assert prompts.parse_polish_json("") is None
    assert prompts.parse_polish_json("   ") is None
    assert prompts.parse_polish_json("not json") is None
    assert prompts.parse_polish_json('["a"]') is None  # a list is not a blob
    big = '{"a": "' + "x" * prompts.POLISH_JSON_MAX + '"}'
    assert prompts.parse_polish_json(big) is None
