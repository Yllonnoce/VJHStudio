from pathlib import Path

import pytest

from vjhstudio import boot, config, db
from vjhstudio.services import projects


@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    return paths, info.session_factory


def test_create_unique_slug_and_dir(env):
    paths, f = env
    with db.session_scope(f) as s:
        a = projects.create(s, paths, "My Project!")
        b = projects.create(s, paths, "my project")
        assert a.slug == "my-project" and b.slug == "my-project-2"
        assert (paths.outputs / "my-project-2").is_dir()
        assert [p.slug for p in projects.list_active(s)] == [
            "default",
            "my-project",
            "my-project-2",
        ]
        projects.set_archived(s, b.id, True)
        assert [p.slug for p in projects.list_active(s)] == ["default", "my-project"]
        assert projects.totals(s, a.id) == {"outputs": 0, "cost": 0.0}


def test_slugify_falls_back_and_truncates():
    assert projects.slugify("???") == "project"
    assert projects.slugify("") == "project"
    assert len(projects.slugify("x" * 200)) == 60


def test_get_rename_and_dir_override(env):
    paths, f = env
    with db.session_scope(f) as s:
        p = projects.create(s, paths, "Alpha", description="first")
        assert projects.get(s, p.id).description == "first"
        projects.rename(s, p.id, "Beta", "second")
        again = projects.get(s, p.id)
        assert again.name == "Beta" and again.description == "second"
        assert again.slug == "alpha"  # the slug (and its directory) is stable across renames
    assert projects.dir_for(paths, "alpha") == paths.outputs / "alpha"
    assert projects.dir_for(paths, "alpha", "/tmp/elsewhere") == Path("/tmp/elsewhere/alpha")
