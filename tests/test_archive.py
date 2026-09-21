import io
import json
import os
import time
import zipfile

import pytest
from PIL import Image

from vjhstudio import __version__, boot, config, db, models
from vjhstudio.services import archive, assets, migrate, projects


def _png(size: int = 32) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (size, size), (9, 8, 7)).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "data")})
    info = boot.boot(paths)
    yield paths, info.session_factory, info.engine
    info.engine.dispose()


def seed_media(paths, factory):
    """One asset upload + one output (file, sidecar, thumb) on disk and in the DB."""
    with db.session_scope(factory) as s:
        project = s.query(models.Project).filter_by(slug="default").one()
        pid = project.id
        job = models.Job(
            id="job-1",
            project_id=pid,
            kind="image",
            status=models.JobStatus.succeeded.value,
            model_air="runware:101@1",
            request_json={},
        )
        s.add(job)
        s.flush()
        root = projects.root_for(s, paths)
        folder = root / "default"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "out-1.png").write_bytes(_png())
        (folder / "out-1.json").write_text(json.dumps({"prompt": "fox"}), encoding="utf-8")
        paths.thumbs.mkdir(parents=True, exist_ok=True)
        (paths.thumbs / "out-1.jpg").write_bytes(_png())
        s.add(
            models.Output(
                job_id=job.id,
                project_id=pid,
                kind="image",
                filename="out-1.png",
                rel_path="default/out-1.png",
                sidecar_rel_path="default/out-1.json",
                thumb_rel_path="thumbs/out-1.jpg",
                model_air="runware:101@1",
                prompt_text="fox",
                params_json={},
            )
        )
        asset, _ = assets.store_upload(
            s, paths, original_name="ref.png", content=_png(48), mime="image/png"
        )
        return {"asset_filename": asset.filename, "project_id": pid}


def _names(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return set(zf.namelist())


# --- create -----------------------------------------------------------------


def test_db_only_archive_holds_manifest_and_db(env):
    paths, factory, _ = env
    dest = archive.create_archive(factory, paths)
    assert dest.parent == paths.backups
    assert dest.name.startswith(archive.ARCHIVE_PREFIX) and dest.suffix == ".zip"
    assert _names(dest) == {archive.MANIFEST, archive.DB_NAME}
    m = archive.manifest_of(dest)
    assert m["includes"] == ["db"]
    assert m["counts"]["projects"] == 1
    assert m["app_version"] == __version__
    assert m["schema_revision"] == migrate.head()
    assert m["created_at"] and m["host"]


def test_archive_with_media_stores_files_uncompressed(env):
    paths, factory, _ = env
    info = seed_media(paths, factory)
    dest = archive.create_archive(factory, paths, uploads=True, outputs=True)
    names = _names(dest)
    assert f"uploads/{info['asset_filename']}" in names
    assert "outputs/default/out-1.png" in names
    assert "outputs/default/out-1.json" in names
    assert "thumbs/out-1.jpg" in names
    with zipfile.ZipFile(dest) as zf:
        assert zf.testzip() is None
        assert zf.getinfo(archive.DB_NAME).compress_type == zipfile.ZIP_DEFLATED
        assert zf.getinfo(archive.MANIFEST).compress_type == zipfile.ZIP_DEFLATED
        for n in ("outputs/default/out-1.png", f"uploads/{info['asset_filename']}"):
            assert zf.getinfo(n).compress_type == zipfile.ZIP_STORED
    m = archive.manifest_of(dest)
    assert m["includes"] == ["db", "uploads", "outputs"]
    assert m["counts"]["outputs"] == 1 and m["counts"]["assets"] == 1


def test_archive_honours_the_outputs_dir_setting(env, tmp_path):
    from vjhstudio.services import settings as settings_svc

    paths, factory, _ = env
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "default").mkdir(parents=True)
    with db.session_scope(factory) as s:
        settings_svc.set_many(s, {"paths.outputs_dir": str(elsewhere)})
    seed_media(paths, factory)
    dest = archive.create_archive(factory, paths, outputs=True)
    assert "outputs/default/out-1.png" in _names(dest)


# --- manifest / naming ------------------------------------------------------


def test_manifest_of_rejects_a_text_file(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello", encoding="utf-8")
    with pytest.raises(archive.ArchiveError):
        archive.manifest_of(f)


def test_manifest_of_rejects_a_foreign_zip(tmp_path):
    z = tmp_path / "other.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "nope")
    with pytest.raises(archive.ArchiveError):
        archive.manifest_of(z)


def test_manifest_of_rejects_a_zip_without_the_database(tmp_path):
    z = tmp_path / "half.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(archive.MANIFEST, json.dumps({"includes": ["db"]}))
    with pytest.raises(archive.ArchiveError):
        archive.manifest_of(z)


@pytest.mark.parametrize(
    "name",
    ["../etc/passwd", "x.zip", "", "vjhstudio-backup-1.txt", "sub/vjhstudio-backup-1.zip", ".."],
)
def test_archive_path_rejects_unsafe_names(env, name):
    paths, _, _ = env
    with pytest.raises(archive.ArchiveError):
        archive.archive_path(paths, name)


def test_archive_path_accepts_a_real_name(env):
    paths, factory, _ = env
    dest = archive.create_archive(factory, paths)
    assert archive.archive_path(paths, dest.name) == dest


# --- list / delete / import -------------------------------------------------


def test_list_archives_is_newest_first_and_skips_corrupt_zips(env):
    paths, factory, _ = env
    first = archive.create_archive(factory, paths)
    time.sleep(1.05)  # the stamp has second resolution
    second = archive.create_archive(factory, paths, uploads=True)
    (paths.backups / f"{archive.ARCHIVE_PREFIX}20990101-000000.zip").write_bytes(b"not a zip")
    listed = archive.list_archives(paths)
    assert [a.name for a in listed] == [second.name, first.name]
    newest = listed[0]
    assert newest.includes == ["db", "uploads"]
    assert newest.size_bytes == second.stat().st_size
    assert newest.app_version == __version__ and newest.schema_revision == migrate.head()
    assert newest.counts["projects"] == 1


def test_list_archives_on_a_missing_directory(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "nope")})
    assert archive.list_archives(paths) == []


def test_delete_archive(env):
    paths, factory, _ = env
    dest = archive.create_archive(factory, paths)
    assert archive.delete_archive(paths, dest.name) is True
    assert not dest.exists()
    assert archive.delete_archive(paths, dest.name) is False


def test_import_archive_round_trips(env, tmp_path):
    paths, factory, _ = env
    made = archive.create_archive(factory, paths)
    content = made.read_bytes()
    made.unlink()
    dest = archive.import_archive(paths, "vjhstudio-backup-20200101-000000.zip", content)
    assert dest.parent == paths.backups and dest.read_bytes() == content
    again = archive.import_archive(paths, "vjhstudio-backup-20200101-000000.zip", content)
    assert again != dest and again.exists()
    assert len(archive.list_archives(paths)) == 2


def test_import_archive_rejects_a_non_archive(env):
    paths, _, _ = env
    with pytest.raises(archive.ArchiveError):
        archive.import_archive(paths, "evil.zip", b"definitely not a zip")
    assert list(paths.backups.glob("*.zip")) == []
    assert list(paths.backups.glob("*.part")) == []


def test_import_archive_renames_a_foreign_filename(env):
    paths, factory, _ = env
    content = archive.create_archive(factory, paths).read_bytes()
    dest = archive.import_archive(paths, "../../evil.zip", content)
    assert dest.parent == paths.backups
    assert dest.name.startswith(archive.ARCHIVE_PREFIX) and dest.name.endswith(".zip")


def test_import_archive_file_streams_and_matches_the_bytes_entry_point(env, tmp_path):
    """The two entry points share one implementation: same validation, same naming."""
    paths, factory, _ = env
    made = archive.create_archive(factory, paths)
    source = tmp_path / "carried-over.zip"
    source.write_bytes(made.read_bytes())
    made.unlink()
    with source.open("rb") as fh:
        dest = archive.import_archive_file(paths, source.name, fh)
    assert dest.parent == paths.backups and dest.read_bytes() == source.read_bytes()
    # A foreign filename is renamed here too, so the list only ever holds our names.
    assert dest.name.startswith(archive.ARCHIVE_PREFIX) and dest.name.endswith(".zip")
    assert len(archive.list_archives(paths)) == 1


def test_import_archive_file_rejects_a_non_archive_and_leaves_nothing(env, tmp_path):
    paths, _, _ = env
    junk = tmp_path / "notes.txt"
    junk.write_bytes(b"definitely not a zip")
    with pytest.raises(archive.ArchiveError), junk.open("rb") as fh:
        archive.import_archive_file(paths, junk.name, fh)
    assert list(paths.backups.glob("*")) == []


# --- restore ----------------------------------------------------------------


def test_restore_replace_round_trip(env, tmp_path):
    paths, factory, engine = env
    info = seed_media(paths, factory)
    dest = archive.create_archive(factory, paths, uploads=True, outputs=True)

    # Wipe: drop the media files and start from an empty database.
    with db.session_scope(factory) as s:
        root = projects.root_for(s, paths)
    engine.dispose()
    (root / "default" / "out-1.png").unlink()
    (root / "default" / "out-1.json").unlink()
    (paths.uploads / info["asset_filename"]).unlink()
    paths.db.unlink()
    migrate.upgrade(paths.db)

    result = archive.restore_replace(paths, dest)

    assert result.skipped == [] and result.outputs_root == root
    assert result.safety_backup is not None and result.safety_backup.exists()
    assert result.safety_backup.name.startswith("vjh-")
    assert result.safety_backup.name.endswith("-pre-restore.db")
    assert result.missing_outputs == 0 and result.missing_assets == 0
    assert result.counts["outputs"] == 1 and result.counts["assets"] == 1
    assert result.counts["projects"] == 1 and result.counts["jobs"] == 1
    assert (root / "default" / "out-1.png").exists()
    assert (paths.uploads / info["asset_filename"]).exists()

    fresh = db.make_engine(paths.db)
    try:
        f2 = db.make_session_factory(fresh)
        with db.session_scope(f2) as s:
            out = s.query(models.Output).one()
            assert out.rel_path == "default/out-1.png" and out.is_missing is False
            assert s.query(models.Asset).count() == 1
    finally:
        fresh.dispose()


def test_db_only_restore_marks_outputs_missing(env):
    paths, factory, engine = env
    seed_media(paths, factory)
    dest = archive.create_archive(factory, paths)
    with db.session_scope(factory) as s:
        root = projects.root_for(s, paths)
    engine.dispose()
    (root / "default" / "out-1.png").unlink()

    result = archive.restore_replace(paths, dest)
    assert result.missing_outputs == 1
    assert result.missing_assets == 0

    fresh = db.make_engine(paths.db)
    try:
        f2 = db.make_session_factory(fresh)
        with db.session_scope(f2) as s:
            assert s.query(models.Output).one().is_missing is True
    finally:
        fresh.dispose()


def test_restore_upgrades_an_older_snapshot(env, tmp_path):
    paths, factory, engine = env
    old_db = tmp_path / "old.db"
    migrate.upgrade(old_db, "0001")
    assert migrate.current(old_db) == "0001"
    z = tmp_path / f"{archive.ARCHIVE_PREFIX}20200101-000000.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(
            archive.MANIFEST,
            json.dumps(
                {
                    "app_version": "0.0.1",
                    "schema_revision": "0001",
                    "created_at": "2020-01-01T00:00:00",
                    "host": "old",
                    "includes": ["db"],
                    "counts": {},
                }
            ),
        )
        zf.write(old_db, archive.DB_NAME)
    engine.dispose()

    result = archive.restore_replace(paths, z)
    assert migrate.current(paths.db) == migrate.head()
    assert result.counts["projects"] == 0


def test_restore_calls_migrate_upgrade(env, monkeypatch):
    paths, factory, engine = env
    dest = archive.create_archive(factory, paths)
    engine.dispose()
    seen = []
    real = migrate.upgrade
    monkeypatch.setattr(archive.migrate, "upgrade", lambda p, *a, **k: seen.append(p) or real(p))
    archive.restore_replace(paths, dest)
    assert seen == [paths.db]


def test_restore_refuses_while_a_job_is_queued(env):
    paths, factory, engine = env
    dest = archive.create_archive(factory, paths)
    with db.session_scope(factory) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
        s.add(
            models.Job(
                id="job-q",
                project_id=pid,
                kind="image",
                status=models.JobStatus.queued.value,
                model_air="runware:101@1",
                request_json={},
            )
        )
    engine.dispose()
    before = (paths.db.stat().st_mtime_ns, paths.db.read_bytes())
    with pytest.raises(archive.ArchiveError) as e:
        archive.restore_replace(paths, dest)
    assert "job" in str(e.value).lower()
    assert (paths.db.stat().st_mtime_ns, paths.db.read_bytes()) == before
    assert list(paths.backups.glob("*-pre-restore.db")) == []


def test_restore_refuses_when_the_caller_reports_running_jobs(env):
    paths, factory, engine = env
    dest = archive.create_archive(factory, paths)
    engine.dispose()
    before = paths.db.read_bytes()
    with pytest.raises(archive.ArchiveError):
        archive.restore_replace(paths, dest, jobs_running=1)
    assert paths.db.read_bytes() == before


def test_restore_rejects_a_member_escaping_the_data_dir(env, tmp_path):
    paths, factory, engine = env
    good = archive.create_archive(factory, paths)
    evil = tmp_path / f"{archive.ARCHIVE_PREFIX}20990101-000000.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(evil, "w") as zf:
        zf.writestr(archive.MANIFEST, src.read(archive.MANIFEST))
        zf.writestr(archive.DB_NAME, src.read(archive.DB_NAME))
        zf.writestr("../../evil.txt", "pwned")
    engine.dispose()
    before = paths.db.read_bytes()
    with pytest.raises(archive.ArchiveError):
        archive.restore_replace(paths, evil)
    assert paths.db.read_bytes() == before
    assert not (paths.data.parent.parent / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_restore_rejects_an_absolute_member(env, tmp_path):
    paths, factory, engine = env
    good = archive.create_archive(factory, paths)
    evil = tmp_path / f"{archive.ARCHIVE_PREFIX}20990102-000000.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(evil, "w") as zf:
        zf.writestr(archive.MANIFEST, src.read(archive.MANIFEST))
        zf.writestr(archive.DB_NAME, src.read(archive.DB_NAME))
        zf.writestr("/tmp/evil.txt", "pwned")
    engine.dispose()
    with pytest.raises(archive.ArchiveError):
        archive.restore_replace(paths, evil)


def test_restore_removes_wal_and_shm_sidecars(env):
    paths, factory, engine = env
    dest = archive.create_archive(factory, paths)
    engine.dispose()
    wal = paths.db.with_name(paths.db.name + "-wal")
    shm = paths.db.with_name(paths.db.name + "-shm")
    wal.write_bytes(b"stale")
    shm.write_bytes(b"stale")
    archive.restore_replace(paths, dest)
    assert not shm.exists()
    assert not wal.exists() or wal.read_bytes() != b"stale"


def test_restore_disposes_the_engine_it_is_given(env):
    paths, factory, engine = env
    dest = archive.create_archive(factory, paths)
    calls = []
    original = engine.dispose
    engine.dispose = lambda *a, **k: calls.append(1) or original()  # type: ignore[method-assign]
    archive.restore_replace(paths, dest, engine=engine)
    assert calls


def test_restore_without_an_existing_database(env):
    paths, factory, engine = env
    dest = archive.create_archive(factory, paths)
    engine.dispose()
    paths.db.unlink()
    result = archive.restore_replace(paths, dest)
    assert result.safety_backup is None
    assert result.counts["projects"] == 1
    assert paths.db.exists()


def test_restore_rejects_a_non_archive(env, tmp_path):
    paths, _, _ = env
    f = tmp_path / "notes.txt"
    f.write_text("hello", encoding="utf-8")
    with pytest.raises(archive.ArchiveError):
        archive.restore_replace(paths, f)


def test_running_jobs_counts_queued_and_running(env):
    paths, factory, _ = env
    assert archive.running_jobs(paths) == 0
    with db.session_scope(factory) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
        for i, status in enumerate((models.JobStatus.queued, models.JobStatus.running)):
            s.add(
                models.Job(
                    id=f"j{i}",
                    project_id=pid,
                    kind="image",
                    status=status.value,
                    model_air="m",
                    request_json={},
                )
            )
    assert archive.running_jobs(paths) == 2


def test_running_jobs_on_a_database_without_tables(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "d")})
    config.ensure_dirs(paths)
    assert archive.running_jobs(paths) == 0
    paths.db.write_bytes(b"")
    assert archive.running_jobs(paths) == 0


def test_archive_leaves_no_temporary_files(env):
    paths, factory, _ = env
    seed_media(paths, factory)
    archive.create_archive(factory, paths, uploads=True, outputs=True)
    assert list(paths.backups.glob("*.part")) == []
    assert sorted(p.name for p in paths.data.iterdir() if p.is_dir()) == sorted(
        ["backups", "outputs", "secrets", "thumbs", "uploads"]
    )


def test_archive_skips_rows_whose_files_are_gone(env):
    paths, factory, engine = env
    seed_media(paths, factory)
    with db.session_scope(factory) as s:
        root = projects.root_for(s, paths)
    (root / "default" / "out-1.png").unlink()
    dest = archive.create_archive(factory, paths, uploads=True, outputs=True)
    names = _names(dest)
    assert "outputs/default/out-1.png" not in names
    assert "outputs/default/out-1.json" in names


def test_restore_overwrites_an_existing_media_file(env):
    paths, factory, engine = env
    info = seed_media(paths, factory)
    dest = archive.create_archive(factory, paths, uploads=True, outputs=True)
    with db.session_scope(factory) as s:
        root = projects.root_for(s, paths)
    engine.dispose()
    (root / "default" / "out-1.png").write_bytes(b"garbage")
    archive.restore_replace(paths, dest)
    assert (root / "default" / "out-1.png").read_bytes() != b"garbage"
    assert (paths.uploads / info["asset_filename"]).exists()
    assert os.path.getsize(root / "default" / "out-1.png") > 0


# --- review fixes -----------------------------------------------------------


def _rezip(source_zip, extra: dict[str, bytes], dest):
    """A copy of `source_zip` with extra members bolted on."""
    with zipfile.ZipFile(source_zip) as src, zipfile.ZipFile(dest, "w") as zf:
        for info in src.infolist():
            zf.writestr(info.filename, src.read(info.filename))
        for name, data in extra.items():
            zf.writestr(name, data)
    return dest


def test_restore_writes_only_media_members(env, tmp_path):
    paths, factory, engine = env
    seed_media(paths, factory)
    good = archive.create_archive(factory, paths, uploads=True, outputs=True)
    evil = _rezip(
        good,
        {
            "secrets/api_key": "stolen-key",
            "vjh.db-wal": b"stale wal",
            "backups/vjh-19700101-000000-manual.db": b"junk",
            "install.json": b"{}",
        },
        tmp_path / f"{archive.ARCHIVE_PREFIX}20990301-000000.zip",
    )
    paths.api_key_file.write_text("real-key", encoding="utf-8")
    engine.dispose()

    result = archive.restore_replace(paths, evil)

    assert paths.api_key_file.read_text(encoding="utf-8") == "real-key"
    assert not paths.db.with_name(paths.db.name + "-wal").exists()
    assert not (paths.backups / "vjh-19700101-000000-manual.db").exists()
    assert not (paths.data / "install.json").exists()
    assert sorted(result.skipped) == [
        "backups/vjh-19700101-000000-manual.db",
        "install.json",
        "secrets/api_key",
        "vjh.db-wal",
    ]
    # the media members it does own still arrived
    assert (paths.outputs / "default" / "out-1.png").exists()


def test_restore_with_nothing_skipped_reports_an_empty_list(env):
    paths, factory, engine = env
    seed_media(paths, factory)
    dest = archive.create_archive(factory, paths, uploads=True, outputs=True)
    engine.dispose()
    assert archive.restore_replace(paths, dest).skipped == []


def test_restore_ignores_an_outputs_root_outside_the_data_dir(env, tmp_path):
    """The restored `paths.outputs_dir` is untrusted: an archive must not be able to
    write into an arbitrary directory by shipping a settings row."""
    from vjhstudio.services import settings as settings_svc

    paths, factory, engine = env
    outside = tmp_path / "outside"
    (outside / "default").mkdir(parents=True)
    with db.session_scope(factory) as s:
        settings_svc.set_many(s, {"paths.outputs_dir": str(outside)})
    seed_media(paths, factory)
    dest = archive.create_archive(factory, paths, outputs=True)
    engine.dispose()
    for f in (outside / "default").iterdir():
        f.unlink()

    result = archive.restore_replace(paths, dest)

    assert result.outputs_root == paths.outputs
    assert (paths.outputs / "default" / "out-1.png").exists()
    assert not (outside / "default" / "out-1.png").exists()
    # and the poisoned setting is reset, so the app agrees with where the files went
    fresh = db.make_engine(paths.db)
    try:
        f2 = db.make_session_factory(fresh)
        with db.session_scope(f2) as s:
            assert projects.root_for(s, paths) == paths.outputs
            assert s.query(models.Output).one().is_missing is False
    finally:
        fresh.dispose()


def test_restore_keeps_an_outputs_root_inside_the_data_dir(env):
    from vjhstudio.services import settings as settings_svc

    paths, factory, engine = env
    inside = paths.data / "media"
    (inside / "default").mkdir(parents=True)
    with db.session_scope(factory) as s:
        settings_svc.set_many(s, {"paths.outputs_dir": str(inside)})
    seed_media(paths, factory)
    dest = archive.create_archive(factory, paths, outputs=True)
    engine.dispose()
    (inside / "default" / "out-1.png").unlink()

    result = archive.restore_replace(paths, dest)
    assert result.outputs_root == inside
    assert (inside / "default" / "out-1.png").exists()


def test_restore_clears_is_missing_when_the_file_comes_back(env):
    paths, factory, engine = env
    seed_media(paths, factory)
    with db.session_scope(factory) as s:
        s.query(models.Output).one().is_missing = True
    dest = archive.create_archive(factory, paths, outputs=True)
    engine.dispose()
    result = archive.restore_replace(paths, dest)
    assert result.missing_outputs == 0
    fresh = db.make_engine(paths.db)
    try:
        f2 = db.make_session_factory(fresh)
        with db.session_scope(f2) as s:
            assert s.query(models.Output).one().is_missing is False
    finally:
        fresh.dispose()


def test_failed_upgrade_puts_the_safety_backup_back(env, monkeypatch):
    paths, factory, engine = env
    with db.session_scope(factory) as s:
        s.add(models.Project(name="Only In The Archive", slug="archived-only"))
    dest = archive.create_archive(factory, paths)
    with db.session_scope(factory) as s:
        s.query(models.Project).filter_by(slug="archived-only").delete()
    engine.dispose()
    before = paths.db.read_bytes()

    def boom(*_a, **_k):
        raise migrate.MigrationFailed("nope")

    monkeypatch.setattr(archive.migrate, "upgrade", boom)
    with pytest.raises(archive.ArchiveError) as e:
        archive.restore_replace(paths, dest)

    safety = next(paths.backups.glob("vjh-*-pre-restore.db"))
    assert safety.name in str(e.value)
    assert "could not be migrated" in str(e.value)
    # the live database is the one we had, not the archived one
    fresh = db.make_engine(paths.db)
    try:
        f2 = db.make_session_factory(fresh)
        with db.session_scope(f2) as s:
            assert s.query(models.Project).filter_by(slug="archived-only").count() == 0
            assert s.query(models.Project).filter_by(slug="default").count() == 1
    finally:
        fresh.dispose()
    assert len(paths.db.read_bytes()) == len(before)
    assert not paths.db.with_name(paths.db.name + "-wal").exists()


def test_failed_upgrade_without_a_previous_database(env, monkeypatch):
    paths, factory, engine = env
    dest = archive.create_archive(factory, paths)
    engine.dispose()
    paths.db.unlink()

    def boom(*_a, **_k):
        raise migrate.MigrationFailed("nope")

    monkeypatch.setattr(archive.migrate, "upgrade", boom)
    with pytest.raises(archive.ArchiveError) as e:
        archive.restore_replace(paths, dest)
    assert "no database to put back" in str(e.value)
    assert not paths.db.exists()


def test_restore_refuses_an_archive_that_expands_too_far(env):
    paths, factory, engine = env
    seed_media(paths, factory)
    dest = archive.create_archive(factory, paths, uploads=True, outputs=True)
    engine.dispose()
    before = paths.db.read_bytes()
    with pytest.raises(archive.ArchiveError) as e:
        archive.restore_replace(paths, dest, max_bytes=1)
    assert "expands to more than" in str(e.value)
    assert paths.db.read_bytes() == before
    assert list(paths.backups.glob("*-pre-restore.db")) == []


def test_the_default_expanded_size_cap_is_generous(env):
    paths, factory, engine = env
    dest = archive.create_archive(factory, paths)
    engine.dispose()
    assert archive.MAX_RESTORE_BYTES == 20 * 1024**3
    archive.restore_replace(paths, dest)  # nowhere near the cap
