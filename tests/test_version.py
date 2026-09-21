import re

import vjhstudio
from vjhstudio import __version__
from vjhstudio.config import REPO_ROOT


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", vjhstudio.__version__)


def test_version_is_0_2_0():
    assert __version__ == "0.2.0"


async def test_health_reports_the_version(client):
    r = await client.get("/api/health")
    assert r.json()["version"] == __version__


async def test_footer_renders_the_version(client):
    r = await client.get("/")
    assert f"v{__version__}" in r.text


def test_changelog_has_a_0_2_0_section_mentioning_update_backup_and_install():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.2.0" in text
    section = text.split("## 0.2.0", 1)[1].split("\n## ", 1)[0].lower()
    assert "update" in section
    assert "backup" in section
    assert "install" in section
