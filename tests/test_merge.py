"""Additive merge of a backup archive into a live install, plus its dry-run preview.

Every test builds two real data directories (A: the source of the archive, B: the
install that is merged into), so the union rules are exercised end to end against
real sqlite files and real media on disk. No network, no fixtures shared with the app.
"""

import io
import json
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from vjhstudio import boot, config, db, models
from vjhstudio.config import Paths
from vjhstudio.services import archive, assets, projects
from vjhstudio.services import settings as settings_svc


def _png(size: int = 32, colour=(9, 8, 7)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(b, "PNG")
    return b.getvalue()


@dataclass
class Install:
    paths: Paths
    factory: sessionmaker[Session]

    def counts(self) -> dict[str, int]:
        with db.session_scope(self.factory) as s:
            return {m.__tablename__: s.query(m).count() for m in archive.COUNT_MODELS}

    def root(self) -> Path:
        with db.session_scope(self.factory) as s:
            return projects.root_for(s, self.paths)


@pytest.fixture
def make_install(tmp_path):
    engines = []

    def make(name: str) -> Install:
        paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / name)})
        info = boot.boot(paths)
        engines.append(info.engine)
        return Install(paths, info.session_factory)

    yield make
    for e in engines:
        e.dispose()


FORM = {"subject": "a fox", "style": "ink"}


def add_project(inst: Install, slug: str, name: str) -> int:
    with db.session_scope(inst.factory) as s:
        p = models.Project(name=name, slug=slug)
        s.add(p)
        s.flush()
        projects.dir_for(inst.paths, slug, projects.root_override(s)).mkdir(
            parents=True, exist_ok=True
        )
        return p.id


def add_prompt(inst: Install, project_id: int | None, title: str, form=None, final="a fox, ink"):
    with db.session_scope(inst.factory) as s:
        p = models.Prompt(
            project_id=project_id,
            title=title,
            kind="image",
            form_json=dict(FORM if form is None else form),
            composed_prompt="a fox, ink",
            final_prompt=final,
            negative_prompt="blurry",
        )
        s.add(p)
        s.flush()
        return p.id


def add_asset(inst: Install, content: bytes, original_name: str) -> str:
    with db.session_scope(inst.factory) as s:
        asset, _ = assets.store_upload(
            s, inst.paths, original_name=original_name, content=content, mime="image/png"
        )
        return asset.filename


def add_job(
    inst: Install,
    job_id: str,
    project_id: int,
    prompt_id: int | None = None,
    status: str = "succeeded",
) -> None:
    with db.session_scope(inst.factory) as s:
        s.add(
            models.Job(
                id=job_id,
                project_id=project_id,
                prompt_id=prompt_id,
                kind="image",
                status=status,
                model_air="runware:101@1",
                request_json={"positivePrompt": "a fox"},
            )
        )


def add_output(
    inst: Install, job_id: str, project_id: int, slug: str, filename: str, colour=(9, 8, 7)
) -> None:
    with db.session_scope(inst.factory) as s:
        root = projects.root_for(s, inst.paths)
        folder = root / slug
        folder.mkdir(parents=True, exist_ok=True)
        stem = Path(filename).stem
        (folder / filename).write_bytes(_png(colour=colour))
        (folder / f"{stem}.json").write_text(json.dumps({"prompt": "a fox"}), encoding="utf-8")
        inst.paths.thumbs.mkdir(parents=True, exist_ok=True)
        (inst.paths.thumbs / f"{stem}.jpg").write_bytes(_png(16, colour))
        s.add(
            models.Output(
                job_id=job_id,
                project_id=project_id,
                kind="image",
                filename=filename,
                rel_path=f"{slug}/{filename}",
                sidecar_rel_path=f"{slug}/{stem}.json",
                thumb_rel_path=f"thumbs/{stem}.jpg",
                model_air="runware:101@1",
                prompt_text="a fox",
                params_json={},
            )
        )


def add_usage(inst: Install, job_id: str | None, project_id: int, cost: float = 0.25) -> None:
    from datetime import date

    with db.session_scope(inst.factory) as s:
        s.add(
            models.UsageEntry(
                job_id=job_id,
                project_id=project_id,
                task_type="imageInference",
                model_air="runware:101@1",
                cost=cost,
                day=date(2026, 9, 20),
            )
        )


def seeded_source(make_install) -> tuple[Install, Path]:
    """Install A: projects default+alpha, 2 prompts, 1 asset, 1 job, 1 output, 1 usage row."""
    a = make_install("a")
    pid = add_project(a, "alpha", "Alpha")
    prompt_id = add_prompt(a, pid, "First")
    add_prompt(a, pid, "Second", form={"subject": "a bear", "style": "oil"})
    add_asset(a, _png(48), "ref.png")
    add_job(a, "job-a1", pid, prompt_id)
    add_output(a, "job-a1", pid, "alpha", "out-a1.png")
    add_usage(a, "job-a1", pid)
    zip_path = archive.create_archive(a.factory, a.paths, uploads=True, outputs=True)
    return a, zip_path


# --- prompt_key -------------------------------------------------------------


def test_prompt_key_is_stable_and_ignores_dict_order():
    one = archive.prompt_key("image", "a fox", "blurry", {"a": 1, "b": 2}, "a fox")
    two = archive.prompt_key("image", "a fox", "blurry", {"b": 2, "a": 1}, "a fox")
    assert one == two and len(one) == 64


def test_prompt_key_changes_with_every_field():
    base = archive.prompt_key("image", "a fox", "blurry", {"a": 1}, "a fox")
    assert archive.prompt_key("video", "a fox", "blurry", {"a": 1}, "a fox") != base
    assert archive.prompt_key("image", "a cat", "blurry", {"a": 1}, "a fox") != base
    assert archive.prompt_key("image", "a fox", "ugly", {"a": 1}, "a fox") != base
    assert archive.prompt_key("image", "a fox", "blurry", {"a": 2}, "a fox") != base


# --- preview ----------------------------------------------------------------


def test_preview_counts_new_rows(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    report = archive.preview_merge(b.factory, b.paths, zip_path)
    assert report.dry_run is True
    assert report.errors == []
    assert report.counts["projects"].new == 1  # alpha; "default" exists in both
    assert report.counts["projects"].existing == 1
    assert report.counts["prompts"].new == 2
    assert report.counts["assets"].new == 1
    assert report.counts["jobs"].new == 1
    assert report.counts["outputs"].new == 1
    assert report.counts["usage_entries"].new == 1
    assert report.counts["catalog_models"].new == 0  # both installs seed the same curated set
    assert report.app_version and report.schema_revision and report.created_at
    assert report.total_new == 7


def test_preview_writes_nothing(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    before = b.counts()
    files_before = sorted(p.name for p in b.paths.data.rglob("*"))
    archive.preview_merge(b.factory, b.paths, zip_path)
    assert b.counts() == before
    assert not list(b.paths.backups.glob("vjh-*-pre-merge.db"))
    assert sorted(p.name for p in b.paths.data.rglob("*")) == files_before


def test_preview_and_merge_agree(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    preview = archive.preview_merge(b.factory, b.paths, zip_path)
    before = b.counts()
    merged = archive.merge(b.factory, b.paths, zip_path)
    after = b.counts()
    assert merged.dry_run is False
    for table, tc in preview.counts.items():
        assert merged.counts[table].new == tc.new, table
        assert after[table] - before[table] == tc.new, table


# --- merge ------------------------------------------------------------------


def test_merge_inserts_rows_files_and_a_safety_backup(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    report = archive.merge(b.factory, b.paths, zip_path)
    assert report.errors == []
    assert report.safety_backup is not None and report.safety_backup.exists()
    assert list(b.paths.backups.glob("vjh-*-pre-merge.db"))

    root = b.root()
    assert (root / "alpha").is_dir()
    assert (root / "alpha" / "out-a1.png").is_file()
    assert (root / "alpha" / "out-a1.json").is_file()
    assert (b.paths.thumbs / "out-a1.jpg").is_file()
    assert report.counts["outputs"].missing_files == 0

    with db.session_scope(b.factory) as s:
        alpha = s.query(models.Project).filter_by(slug="alpha").one()
        out = s.query(models.Output).one()
        assert out.project_id == alpha.id  # remapped, not the id it had in A
        assert out.is_missing is False
        job = s.query(models.Job).one()
        assert job.id == "job-a1" and job.project_id == alpha.id
        assert job.prompt_id == s.query(models.Prompt).filter_by(title="First").one().id
        usage = s.query(models.UsageEntry).one()
        assert usage.job_id == "job-a1" and usage.project_id == alpha.id
        asset = s.query(models.Asset).one()
        assert (b.paths.uploads / asset.filename).is_file()


def test_merge_is_idempotent(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    first = archive.merge(b.factory, b.paths, zip_path)
    after_first = b.counts()
    second = archive.merge(b.factory, b.paths, zip_path)
    assert b.counts() == after_first
    for table, tc in first.counts.items():
        assert second.counts[table].new == 0, table
        assert second.counts[table].existing == tc.new + tc.existing, table


def test_project_slug_collision_keeps_ours_and_remaps_outputs(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    local_pid = add_project(b, "alpha", "My own alpha")
    report = archive.merge(b.factory, b.paths, zip_path)
    assert report.counts["projects"].new == 0
    assert report.counts["projects"].existing == 2
    with db.session_scope(b.factory) as s:
        alpha = s.query(models.Project).filter_by(slug="alpha").one()
        assert alpha.id == local_pid and alpha.name == "My own alpha"
        assert s.query(models.Output).one().project_id == local_pid
        assert s.query(models.Job).one().project_id == local_pid


def test_asset_matched_by_sha256_is_kept_and_its_file_untouched(make_install):
    content = _png(48)
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    filename = add_asset(b, content, "my-own-name.png")
    path = b.paths.uploads / filename
    path.write_bytes(b"local bytes that must survive")
    report = archive.merge(b.factory, b.paths, zip_path)
    assert report.counts["assets"].new == 0 and report.counts["assets"].existing == 1
    assert path.read_bytes() == b"local bytes that must survive"
    with db.session_scope(b.factory) as s:
        assert s.query(models.Asset).one().original_name == "my-own-name.png"


def test_prompt_matching_ignores_the_title_but_not_the_form(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    pid = add_project(b, "alpha", "Alpha")
    add_prompt(b, pid, "A different title")  # same kind/final/negative/form
    add_prompt(b, pid, "Second", form={"subject": "a bear", "style": "watercolour"})
    report = archive.merge(b.factory, b.paths, zip_path)
    assert report.counts["prompts"].existing == 1
    assert report.counts["prompts"].new == 1  # the oil one differs by a single form field
    with db.session_scope(b.factory) as s:
        titles = sorted(p.title for p in s.query(models.Prompt))
        assert titles == ["A different title", "Second", "Second"]


def test_job_id_collision_is_skipped_and_its_usage_is_not_imported(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    pid = add_project(b, "alpha", "Alpha")
    add_job(b, "job-a1", pid, status="cancelled")
    report = archive.merge(b.factory, b.paths, zip_path)
    assert report.counts["jobs"].new == 0 and report.counts["jobs"].existing == 1
    assert report.counts["usage_entries"].new == 0
    assert report.counts["usage_entries"].existing == 1
    with db.session_scope(b.factory) as s:
        assert s.query(models.Job).one().status == "cancelled"  # ours, untouched
        assert s.query(models.UsageEntry).count() == 0


def test_a_queued_job_arrives_as_failed_and_orphaned(make_install):
    a = make_install("a")
    pid = add_project(a, "alpha", "Alpha")
    add_job(a, "job-q", pid, status="queued")
    add_job(a, "job-r", pid, status="running")
    zip_path = archive.create_archive(a.factory, a.paths)
    b = make_install("b")
    archive.merge(b.factory, b.paths, zip_path)
    with db.session_scope(b.factory) as s:
        for job_id in ("job-q", "job-r"):
            job = s.get(models.Job, job_id)
            assert job.status == models.JobStatus.failed.value
            assert job.error_code == "orphaned"
            assert job.finished_at is not None


def test_output_collision_keeps_ours_and_never_touches_the_file(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    pid = add_project(b, "alpha", "Alpha")
    add_job(b, "job-b1", pid)
    add_output(b, "job-b1", pid, "alpha", "out-a1.png", colour=(200, 0, 0))
    local = b.root() / "alpha" / "out-a1.png"
    before = local.read_bytes()
    report = archive.merge(b.factory, b.paths, zip_path)
    assert report.counts["outputs"].new == 0 and report.counts["outputs"].existing == 1
    assert local.read_bytes() == before
    with db.session_scope(b.factory) as s:
        assert s.query(models.Output).one().job_id == "job-b1"


def test_a_db_only_archive_flags_the_files_it_does_not_carry(make_install):
    a = make_install("a")
    pid = add_project(a, "alpha", "Alpha")
    add_asset(a, _png(48), "ref.png")
    add_job(a, "job-a1", pid)
    add_output(a, "job-a1", pid, "alpha", "out-a1.png")
    zip_path = archive.create_archive(a.factory, a.paths)  # db only
    b = make_install("b")
    report = archive.merge(b.factory, b.paths, zip_path)
    assert report.counts["outputs"].new == 1
    assert report.counts["outputs"].missing_files == 1
    assert report.counts["assets"].missing_files == 1
    with db.session_scope(b.factory) as s:
        assert s.query(models.Output).one().is_missing is True


def test_settings_and_app_meta_are_never_merged(make_install):
    a = make_install("a")
    with db.session_scope(a.factory) as s:
        settings_svc.set_many(s, {"ui.theme": "crimson"})
        s.add(models.AppMeta(key="merge_probe", value="from-a"))
    zip_path = archive.create_archive(a.factory, a.paths)
    b = make_install("b")
    with db.session_scope(b.factory) as s:
        settings_svc.set_many(s, {"ui.theme": "forest"})
        revision_before = s.get(models.AppMeta, "schema_revision").value
    archive.merge(b.factory, b.paths, zip_path)
    with db.session_scope(b.factory) as s:
        assert settings_svc.get(s, "ui.theme") == "forest"
        assert s.get(models.AppMeta, "merge_probe") is None
        assert s.get(models.AppMeta, "schema_revision").value == revision_before


def test_an_output_whose_job_is_unknown_is_skipped_and_reported(make_install):
    a = make_install("a")
    pid = add_project(a, "alpha", "Alpha")
    add_job(a, "job-a1", pid)
    add_output(a, "job-a1", pid, "alpha", "out-a1.png")
    zip_path = archive.create_archive(a.factory, a.paths, outputs=True)
    # Strip the job row out of the archive's database so the output dangles.
    stripped = _without_jobs(zip_path)
    b = make_install("b")
    report = archive.merge(b.factory, b.paths, stripped)
    assert report.counts["outputs"].new == 0
    assert any("out-a1.png" in e for e in report.errors)
    with db.session_scope(b.factory) as s:
        assert s.query(models.Output).count() == 0


def patched_archive(zip_path: Path, *statements: str) -> Path:
    """A copy of an archive whose database has been edited - the only way to build the
    hostile or inconsistent archives a merge has to survive."""
    tmp = zip_path.with_name(f"patched-{len(statements)}-" + zip_path.name)
    raw = zip_path.parent / "patched.db"
    with zipfile.ZipFile(zip_path) as src:
        raw.write_bytes(src.read(archive.DB_NAME))
        conn = sqlite3.connect(raw)
        conn.execute("PRAGMA journal_mode=DELETE")  # keep the edits in the main file
        for sql in statements:
            conn.execute(sql)
        conn.commit()
        conn.close()
        with zipfile.ZipFile(tmp, "w") as dst:
            for info in src.infolist():
                payload = raw.read_bytes() if info.filename == archive.DB_NAME else src.read(info)
                dst.writestr(info.filename, payload)
    raw.unlink()
    return tmp


def _without_jobs(zip_path: Path) -> Path:
    return patched_archive(zip_path, "DELETE FROM jobs")


def test_an_output_path_cannot_escape_the_data_directory(make_install, tmp_path):
    """The rel_path comes out of the archive's database, not its member list, so it is
    attacker-controlled input that `_check_members` never sees."""
    _a, zip_path = seeded_source(make_install)
    hostile = patched_archive(
        zip_path, "UPDATE outputs SET rel_path = '../../../escape.png', filename = 'escape.png'"
    )
    b = make_install("b")
    report = archive.merge(b.factory, b.paths, hostile)
    assert report.counts["outputs"].new == 1
    assert report.counts["outputs"].missing_files == 1
    assert any("escapes the data directory" in e for e in report.errors)
    assert not (tmp_path / "escape.png").exists()
    assert not list(tmp_path.glob("**/escape.png"))
    with db.session_scope(b.factory) as s:
        assert s.query(models.Output).one().is_missing is True


def test_an_asset_file_can_never_land_outside_uploads(make_install, tmp_path):
    _a, zip_path = seeded_source(make_install)
    hostile = patched_archive(zip_path, "UPDATE assets SET filename = '../../stolen.png'")
    b = make_install("b")
    report = archive.merge(b.factory, b.paths, hostile)
    assert report.counts["assets"].missing_files == 1
    assert any("escapes the data directory" in e for e in report.errors)
    assert not list(tmp_path.glob("**/stolen.png"))


def test_a_clashing_asset_filename_is_renamed_rather_than_lost(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    mine = add_asset(b, _png(64), "mine.png")  # different bytes, so a different sha256
    collide = patched_archive(zip_path, f"UPDATE assets SET filename = '{mine}'")
    report = archive.merge(b.factory, b.paths, collide)
    assert report.counts["assets"].new == 1
    with db.session_scope(b.factory) as s:
        names = sorted(a.filename for a in s.query(models.Asset))
    assert len(names) == 2 and mine in names
    assert (b.paths.uploads / mine).read_bytes() == _png(64)  # ours, untouched


def test_merge_rejects_a_zip_that_is_not_an_archive(make_install):
    b = make_install("b")
    junk = b.paths.data / "junk.zip"
    junk.write_bytes(b"not a zip at all")
    with pytest.raises(archive.ArchiveError):
        archive.preview_merge(b.factory, b.paths, junk)
    with pytest.raises(archive.ArchiveError):
        archive.merge(b.factory, b.paths, junk)
    assert not list(b.paths.backups.glob("vjh-*-pre-merge.db"))


def test_merge_leaves_no_temporary_files_behind(make_install, tmp_path):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    archive.merge(b.factory, b.paths, zip_path)
    assert not list(b.paths.data.rglob("*.part"))
    assert not list(b.paths.data.rglob("vjh-merge-*"))


def test_merge_summary_names_the_tables_that_grew(make_install):
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    report = archive.merge(b.factory, b.paths, zip_path)
    text = archive.merge_summary(report)
    assert text.startswith("Merged: ")
    assert "1 project" in text and "1 output" in text
    empty = archive.merge_summary(archive.merge(b.factory, b.paths, zip_path))
    assert "nothing new" in empty


def test_a_merge_never_overwrites_a_file_that_is_already_there(make_install):
    """A file on disk with no row is still the user's file: the merge adopts it."""
    _a, zip_path = seeded_source(make_install)
    b = make_install("b")
    pid = add_project(b, "alpha", "Alpha")
    add_job(b, "job-b1", pid)
    add_output(b, "job-b1", pid, "alpha", "out-a1.png", colour=(200, 0, 0))
    local = b.root() / "alpha" / "out-a1.png"
    before = local.read_bytes()
    with db.session_scope(b.factory) as s:
        s.query(models.Output).delete()
    report = archive.merge(b.factory, b.paths, zip_path)
    assert report.counts["outputs"].new == 1
    assert report.counts["outputs"].missing_files == 0
    assert local.read_bytes() == before
    with db.session_scope(b.factory) as s:
        assert s.query(models.Output).one().is_missing is False


def test_a_file_already_on_disk_is_not_missing_even_in_a_db_only_archive(make_install):
    """The db-only archive carries no file, but ours is right there: the row is not
    flagged missing and nothing is counted against the merge."""
    a = make_install("a")
    pid = add_project(a, "alpha", "Alpha")
    add_job(a, "job-a1", pid)
    add_output(a, "job-a1", pid, "alpha", "out-a1.png")
    zip_path = archive.create_archive(a.factory, a.paths)  # db only
    b = make_install("b")
    local_pid = add_project(b, "alpha", "Alpha")
    add_job(b, "job-b1", local_pid)
    add_output(b, "job-b1", local_pid, "alpha", "out-a1.png", colour=(200, 0, 0))
    with db.session_scope(b.factory) as s:
        s.query(models.Output).delete()
    report = archive.merge(b.factory, b.paths, zip_path)
    assert report.counts["outputs"].new == 1
    assert report.counts["outputs"].missing_files == 0
    with db.session_scope(b.factory) as s:
        assert s.query(models.Output).one().is_missing is False
