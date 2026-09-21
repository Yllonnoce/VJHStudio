"""Settings -> Backups: create, download, import, delete, restore, merge.

The app fixture's data directory is the install being acted on ("A"); a second,
real data directory ("B") is booted per test to produce genuine archives to import,
restore and merge, so nothing here fakes the archive service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from vjhstudio import boot, config, db, models
from vjhstudio.config import Paths
from vjhstudio.services import archive, prompts
from vjhstudio.services import restart as restart_svc


@dataclass
class Install:
    paths: Paths
    factory: sessionmaker[Session]


@pytest.fixture
def other(tmp_path):
    """A second install, booted for real, used as the source of every archive."""
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "other")})
    info = boot.boot(paths)
    try:
        yield Install(paths, info.session_factory)
    finally:
        info.engine.dispose()


def add_project(inst: Install, slug: str, name: str) -> int:
    with db.session_scope(inst.factory) as s:
        p = models.Project(name=name, slug=slug)
        s.add(p)
        s.flush()
        return p.id


def make_archive(inst: Install) -> Path:
    return archive.create_archive(inst.factory, inst.paths)


async def import_archive(client, zip_path: Path):
    return await client.post(
        "/system/backups/import",
        files={"file": (zip_path.name, zip_path.read_bytes(), "application/zip")},
    )


async def create_backup(client) -> str:
    r = await client.post("/system/backup", data={"uploads": "on", "outputs": "on"})
    assert r.status_code == 200
    return only_name(r)


def only_name(response) -> str:
    """The single archive name the panel lists."""
    names = set(re.findall(r"vjhstudio-backup-[A-Za-z0-9._-]+\.zip", response.text))
    assert len(names) == 1, names
    return names.pop()


# --- the Settings section ---------------------------------------------------


async def test_settings_page_has_a_backups_section(client):
    r = await client.get("/settings")
    assert r.status_code == 200
    assert r.text.count('id="backups"') == 1
    assert "Create backup" in r.text


async def test_backups_panel_says_when_there_are_none(client):
    r = await client.get("/hx/system/backups")
    assert r.status_code == 200 and "No backups yet" in r.text


# --- create -----------------------------------------------------------------


async def test_create_backup_writes_a_zip_and_lists_it(client, paths):
    r = await client.post("/system/backup", data={"uploads": "on", "outputs": "on"})
    assert r.status_code == 200
    name = only_name(r)
    assert "Backup created" in r.text
    written = list(paths.backups.glob(f"{archive.ARCHIVE_PREFIX}*.zip"))
    assert [p.name for p in written] == [name]
    assert "uploads" in r.text and "outputs" in r.text
    assert "KB" in r.text or "MB" in r.text


# --- download ---------------------------------------------------------------


async def test_download_serves_the_zip_as_an_attachment(client):
    name = await create_backup(client)
    r = await client.get(f"/system/backups/{name}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"


@pytest.mark.parametrize("name", ["..%2Fx.zip", "x.zip", "vjhstudio-backup-nope.zip"])
async def test_download_refuses_anything_that_is_not_an_archive(client, name):
    r = await client.get(f"/system/backups/{name}/download")
    assert r.status_code == 404


# --- import -----------------------------------------------------------------


async def test_import_rejects_a_file_that_is_not_an_archive(client, tmp_path):
    junk = tmp_path / "notes.txt"
    junk.write_text("hello")
    r = await client.post(
        "/system/backups/import", files={"file": (junk.name, junk.read_bytes(), "text/plain")}
    )
    assert r.status_code == 422 and archive.NOT_AN_ARCHIVE in r.text


async def test_import_accepts_a_real_archive(client, paths, other):
    z = make_archive(other)
    r = await import_archive(client, z)
    assert r.status_code == 200
    assert z.name in r.text
    assert (paths.backups / z.name).is_file()


async def test_import_without_a_file_is_refused(client):
    r = await client.post("/system/backups/import", data={"nothing": "here"})
    assert r.status_code == 422 and "Choose a backup file" in r.text


# --- delete -----------------------------------------------------------------


async def test_delete_removes_the_file(client, paths):
    name = await create_backup(client)
    r = await client.delete(f"/system/backups/{name}")
    assert r.status_code == 200 and "deleted" in r.text.lower()
    assert not (paths.backups / name).exists()
    assert list(paths.backups.glob(f"{archive.ARCHIVE_PREFIX}*.zip")) == []


async def test_delete_refuses_a_name_that_is_not_an_archive(client):
    """A traversal never even reaches the handler (the router 404s it); a plain name
    that is not one of ours is refused by the service, in the panel."""
    r = await client.delete("/system/backups/vjh.db")
    assert r.status_code == 422 and archive.NOT_AN_ARCHIVE in r.text
    assert (await client.delete("/system/backups/..%2Fvjh.db")).status_code == 404


# --- restore ----------------------------------------------------------------


@pytest.fixture
def restarts(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        restart_svc, "request_restart", lambda: calls.append("restart") or "supervisor"
    )
    return calls


async def test_restore_restarts_once_and_redirects(client, other, restarts):
    add_project(other, "imported", "Imported")
    z = make_archive(other)
    assert (await import_archive(client, z)).status_code == 200

    r = await client.post(f"/system/backups/{z.name}/restore")
    assert r.status_code == 200
    assert r.headers["HX-Redirect"].startswith("/restarting")
    assert "return=/settings%23backups" in r.headers["HX-Redirect"]
    assert restarts == ["restart"]
    assert "Restored" in r.text
    assert "skipped" in r.text.lower()


async def test_restore_refuses_while_jobs_run(client, other, restarts, monkeypatch):
    z = make_archive(other)
    assert (await import_archive(client, z)).status_code == 200

    def boom(*a, **k):
        raise archive.ArchiveError(archive.JOBS_RUNNING)

    monkeypatch.setattr(archive, "restore_replace", boom)
    r = await client.post(f"/system/backups/{z.name}/restore")
    assert r.status_code == 422
    assert archive.JOBS_RUNNING in r.text
    assert restarts == []


async def test_restore_refuses_a_name_that_is_not_an_archive(client, restarts):
    r = await client.post("/system/backups/vjh.db/restore")
    assert r.status_code == 422 and archive.NOT_AN_ARCHIVE in r.text
    assert (await client.post("/system/backups/..%2Fvjh.db/restore")).status_code == 404
    assert restarts == []


# --- merge ------------------------------------------------------------------


async def test_preview_then_merge_then_nothing_new(client, other):
    add_project(other, "alpha", "Alpha")
    add_project(other, "beta", "Beta")
    z = make_archive(other)
    assert (await import_archive(client, z)).status_code == 200

    r = await client.post(f"/system/backups/{z.name}/preview-merge")
    assert r.status_code == 200
    assert "Merge now" in r.text
    assert "2" in r.text and "projects" in r.text.lower()
    assert z.name in r.text

    r = await client.post(f"/system/backups/{z.name}/merge")
    assert r.status_code == 200
    assert "Merged:" in r.text and "2 projects" in r.text
    assert r.headers["HX-Trigger"] == "jobs-changed"

    r = await client.post(f"/system/backups/{z.name}/preview-merge")
    assert r.status_code == 200 and "Nothing new to merge." in r.text
    assert "disabled" in r.text


async def test_merge_reports_a_failure_with_its_safety_backup(client, other, monkeypatch):
    z = make_archive(other)
    assert (await import_archive(client, z)).status_code == 200

    def boom(*a, **k):
        raise archive.MergeError("The merge failed: nope", Path("/tmp/vjh-20200101-000000.db"))

    monkeypatch.setattr(archive, "merge", boom)
    r = await client.post(f"/system/backups/{z.name}/merge")
    assert r.status_code == 422
    assert "The merge failed" in r.text and "vjh-20200101-000000.db" in r.text


async def test_merge_dedupes_prompts_by_their_stored_hash(client, app, other):
    """A prompt written by the app's own `upsert` in B, merged into A twice, lands once
    and is findable by the app's own dedupe lookup."""
    pid = add_project(other, "alpha", "Alpha")
    with db.session_scope(other.factory) as s:
        prompt, created = prompts.upsert(
            s,
            project_id=pid,
            kind="image",
            title="A fox",
            form={"subject": "a fox", "style": "ink"},
            final_prompt="a fox, ink, 8k",
            negative_prompt="blurry",
        )
        assert created and prompt.content_hash
        wanted = prompt.content_hash

    z = make_archive(other)
    assert (await import_archive(client, z)).status_code == 200
    for _ in range(2):
        r = await client.post(f"/system/backups/{z.name}/merge")
        assert r.status_code == 200

    with db.session_scope(app.state.boot.session_factory) as s:
        rows = s.query(models.Prompt).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.content_hash == wanted == prompts.hash_of(row)
        assert prompts.find_by_hash(s, row.project_id, prompts.hash_of(row)) is not None
