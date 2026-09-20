# RunwareStudio Phase 1 (Skeleton) Implementation Plan

> Renamed to **VJHStudio** on 2026-09-20 (package `vjhstudio`, env prefix `VJHSTUDIO_`, repo github.com/yllonnoce/VJHStudio).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A bootable RunwareStudio: uv project, config + key stash, full SQLite schema under Alembic, boot sequence with backups, FastAPI app with health endpoint and a Settings page that saves the RunWare key and shows the account balance, CLI, and launcher scripts.

**Architecture:** Single FastAPI process. Sync SQLAlchemy 2 over SQLite (WAL). Alembic migrations run at boot, refusing to start on failure. Jinja2 + HTMX + Alpine server-rendered UI, assets vendored. RunWare reached through the official `runware-sdk` (`await client.run(dict)`), injected via a factory so tests use a fake.

**Tech Stack:** Python 3.12 (uv-managed), FastAPI, uvicorn, SQLAlchemy 2, Alembic, pydantic 2, Jinja2, httpx, runware-sdk 1.6.x, pytest + pytest-asyncio, ruff.

**Spec:** `docs/superpowers/specs/2026-09-20-runwarestudio-design.md` (sections: Architecture, Data model, Migrations/backups, Self-update and restart, Configuration, Testing, Installers and launchers).

## Global Constraints

- Python `>=3.11` in pyproject; develop and lock with 3.12 (`uv python pin 3.12`).
- Dependencies managed only with uv; `uv.lock` committed; never `pip install`.
- Default port **8080**, host `127.0.0.1`. Env prefix `RUNWARESTUDIO_`. Restart exit code **75**. Migration failure exit code **3**.
- Data dir default `<repo>/data/` (git-ignored). Layout: `studio.db`, `backups/`, `uploads/`, `outputs/`, `secrets/api_key`.
- API key: env `RUNWARE_API_KEY` overrides the file; the key is never logged, never rendered back into HTML (masked last 4 only).
- Windows scripts are `.bat` only. **No PowerShell, no `.ps1`, no `powershell -Command`.**
- `runwarestudio/runware/` and `runwarestudio/services/` never import `runwarestudio/web/`.
- No `Base.metadata.create_all` anywhere except tests' throwaway engines; schema comes from Alembic.
- Commit after every task with a conventional message; end commit messages with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Tests: `uv run pytest -q` must pass before each commit.

## File Structure (Phase 1)

```
pyproject.toml                 project, deps, scripts entry `runwarestudio = runwarestudio.main:main`, pytest/ruff config
.python-version                3.12
alembic.ini                    script_location = migrations; sqlalchemy.url blank (set at runtime)
migrations/env.py              binds runwarestudio.models.Base.metadata, render_as_batch=True
migrations/script.py.mako
migrations/versions/0001_initial.py   whole schema from the spec
start.sh, start.bat, scripts/restart_helper.bat
runwarestudio/__init__.py      __version__
runwarestudio/config.py        Paths dataclass, env parsing, RESTART_EXIT_CODE, MIGRATION_FAIL_EXIT_CODE
runwarestudio/secrets.py       read_api_key/write_api_key/clear_api_key/effective_api_key/key_source/mask
runwarestudio/db.py            make_engine(path) (WAL, FK on), make_session_factory, session_scope
runwarestudio/models/*.py      base, project, prompt, catalog, asset, job, output, setting, usage
runwarestudio/services/migrate.py   alembic_config, head, current, needs_upgrade, upgrade, MigrationFailed
runwarestudio/services/backup.py    backup_db(paths, label), rotate(paths, label, keep)
runwarestudio/services/settings.py  SPEC, get, set_many, all_values
runwarestudio/services/gitinfo.py   is_git_install, current_commit
runwarestudio/services/account.py   refresh_balance(client_factory, api_key, session) -> BalanceInfo
runwarestudio/boot.py          boot(paths) -> BootInfo; orphan_jobs
runwarestudio/runware/client.py     open_client(api_key, transport) async ctx manager; ClientFactory type
runwarestudio/runware/errors.py     classify(RunwareError) -> UserFacingError
runwarestudio/web/app.py       create_app(paths, client_factory=open_client) with lifespan
runwarestudio/web/deps.py      templates, get_session dependency, is_hx(request)
runwarestudio/web/routes/system.py   GET /api/health
runwarestudio/web/routes/pages.py    GET / (dashboard placeholder), GET /settings
runwarestudio/web/routes/settings.py POST /settings, POST /settings/api-key, POST /settings/api-key/test, GET /hx/header/balance
runwarestudio/web/templates/base.html, _header.html, partials/_balance_chip.html, partials/_toast.html,
                              pages/index.html, pages/settings.html, settings/_api_key_form.html, settings/_general_form.html
runwarestudio/web/static/vendor/{htmx.min.js,alpine.min.js,pico.min.css}, css/app.css, js/app.js
runwarestudio/main.py          CLI: serve, migrate, version, doctor
tests/conftest.py              tmp paths, migrated db, app + client fixtures, FakeRunware
tests/fakes/fake_runware.py
tests/test_*.py                one per module above
```

---

### Task 1: Project scaffold with uv

**Files:**
- Create: `pyproject.toml`, `.python-version`, `runwarestudio/__init__.py`, `tests/__init__.py`, `tests/test_version.py`, `README.md`

**Interfaces:**
- Produces: `runwarestudio.__version__: str` (single source of truth; pyproject reads it dynamically).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_version.py
import re
import runwarestudio

def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", runwarestudio.__version__)
```

- [ ] **Step 2: Create pyproject and package**

```toml
# pyproject.toml
[project]
name = "runwarestudio"
dynamic = ["version"]
description = "Local image and video generation studio for RunWare.AI"
requires-python = ">=3.11"
license = "MIT"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "jinja2>=3.1",
  "python-multipart>=0.0.9",
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "pydantic>=2.7",
  "httpx>=0.27",
  "runware-sdk>=1.6,<2",
  "pillow>=10",
  "python-slugify>=8",
]

[project.scripts]
runwarestudio = "runwarestudio.main:main"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.5"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.version]
path = "runwarestudio/__init__.py"

[tool.hatch.build.targets.wheel]
packages = ["runwarestudio"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

```python
# runwarestudio/__init__.py
"""RunwareStudio: local image and video generation studio for RunWare.AI."""
__version__ = "0.1.0"
```

`tests/__init__.py` is empty. `README.md`: one paragraph naming the project, the spec path, and `uv sync && uv run runwarestudio serve`.

- [ ] **Step 3: Pin Python, sync, run test**

Run: `uv python pin 3.12 && uv sync && uv run pytest -q`
Expected: `1 passed`; `uv.lock` and `.venv/` created (`.venv/` is git-ignored already).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .python-version uv.lock runwarestudio/__init__.py tests/__init__.py tests/test_version.py README.md
git commit -m "chore: scaffold runwarestudio project with uv

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Configuration and paths

**Files:**
- Create: `runwarestudio/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `RESTART_EXIT_CODE = 75`, `MIGRATION_FAIL_EXIT_CODE = 3`, `DEFAULT_PORT = 8080`, `DEFAULT_HOST = "127.0.0.1"`, `REPO_ROOT: Path`
  - `@dataclass(frozen=True) class Paths: data, db, backups, uploads, outputs, secrets, api_key_file` (all `Path`)
  - `def resolve_paths(env: Mapping[str, str] | None = None) -> Paths`
  - `def ensure_dirs(paths: Paths) -> None` (mkdir -p all; chmod 0o700 on `secrets` when not Windows)
  - `def env_int(env, name, default) -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
import os, stat, sys
from pathlib import Path
from runwarestudio import config

def test_default_data_dir_is_repo_data():
    p = config.resolve_paths(env={})
    assert p.data == config.REPO_ROOT / "data"
    assert p.db == p.data / "studio.db"
    assert p.api_key_file == p.data / "secrets" / "api_key"

def test_env_overrides_data_dir(tmp_path):
    p = config.resolve_paths(env={"RUNWARESTUDIO_DATA_DIR": str(tmp_path / "d")})
    assert p.data == tmp_path / "d"
    assert p.outputs == tmp_path / "d" / "outputs"

def test_ensure_dirs_creates_layout(tmp_path):
    p = config.resolve_paths(env={"RUNWARESTUDIO_DATA_DIR": str(tmp_path)})
    config.ensure_dirs(p)
    for d in (p.backups, p.uploads, p.outputs, p.secrets):
        assert d.is_dir()
    if sys.platform != "win32":
        assert stat.S_IMODE(p.secrets.stat().st_mode) == 0o700

def test_env_int_falls_back_on_garbage():
    assert config.env_int({"RUNWARESTUDIO_PORT": "abc"}, "RUNWARESTUDIO_PORT", 8080) == 8080
    assert config.env_int({"RUNWARESTUDIO_PORT": "9000"}, "RUNWARESTUDIO_PORT", 8080) == 9000
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError: runwarestudio.config`.

- [ ] **Step 3: Implement**

```python
# runwarestudio/config.py
"""Environment and filesystem configuration. No I/O beyond mkdir."""
from __future__ import annotations
import os, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
RESTART_EXIT_CODE = 75
MIGRATION_FAIL_EXIT_CODE = 3
DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"
ENV_PREFIX = "RUNWARESTUDIO_"


@dataclass(frozen=True)
class Paths:
    data: Path
    db: Path
    backups: Path
    uploads: Path
    outputs: Path
    secrets: Path
    api_key_file: Path


def resolve_paths(env: Mapping[str, str] | None = None) -> Paths:
    env = os.environ if env is None else env
    raw = env.get(ENV_PREFIX + "DATA_DIR")
    data = Path(raw).expanduser().resolve() if raw else REPO_ROOT / "data"
    secrets = data / "secrets"
    return Paths(
        data=data, db=data / "studio.db", backups=data / "backups",
        uploads=data / "uploads", outputs=data / "outputs",
        secrets=secrets, api_key_file=secrets / "api_key",
    )


def ensure_dirs(paths: Paths) -> None:
    for d in (paths.data, paths.backups, paths.uploads, paths.outputs, paths.secrets):
        d.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(paths.secrets, 0o700)


def env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return default


def env_str(env: Mapping[str, str], name: str, default: str) -> str:
    v = env.get(name)
    return v if v else default
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config.py -q` → Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add runwarestudio/config.py tests/test_config.py
git commit -m "feat: config paths and env parsing

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: API key stash

**Files:**
- Create: `runwarestudio/secrets.py`, `tests/test_secrets.py`

**Interfaces:**
- Produces:
  - `def write_api_key(paths: Paths, key: str) -> None` (strips, refuses empty, writes with mode 0o600)
  - `def read_api_key(paths) -> str | None`
  - `def clear_api_key(paths) -> None`
  - `def effective_api_key(paths, env=None) -> str | None` (env `RUNWARE_API_KEY` wins)
  - `def key_source(paths, env=None) -> Literal["env","file","none"]`
  - `def mask(key: str | None) -> str` → `"••••••••1a2b"` or `""`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_secrets.py
import stat, sys, pytest
from runwarestudio import config, secrets

@pytest.fixture
def paths(tmp_path):
    p = config.resolve_paths(env={"RUNWARESTUDIO_DATA_DIR": str(tmp_path)})
    config.ensure_dirs(p)
    return p

def test_round_trip_and_mode(paths):
    secrets.write_api_key(paths, "  abcdef1234  ")
    assert secrets.read_api_key(paths) == "abcdef1234"
    if sys.platform != "win32":
        assert stat.S_IMODE(paths.api_key_file.stat().st_mode) == 0o600

def test_env_overrides_file(paths):
    secrets.write_api_key(paths, "filekey")
    assert secrets.effective_api_key(paths, env={"RUNWARE_API_KEY": "envkey"}) == "envkey"
    assert secrets.key_source(paths, env={"RUNWARE_API_KEY": "envkey"}) == "env"
    assert secrets.key_source(paths, env={}) == "file"

def test_missing_key(paths):
    assert secrets.read_api_key(paths) is None
    assert secrets.effective_api_key(paths, env={}) is None
    assert secrets.key_source(paths, env={}) == "none"

def test_clear_and_empty_rejected(paths):
    secrets.write_api_key(paths, "k")
    secrets.clear_api_key(paths)
    assert secrets.read_api_key(paths) is None
    with pytest.raises(ValueError):
        secrets.write_api_key(paths, "   ")

def test_mask():
    assert secrets.mask("abcdef1234") == "••••••••1234"
    assert secrets.mask("ab") == "••••••••"
    assert secrets.mask(None) == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_secrets.py -q` → Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# runwarestudio/secrets.py
"""Plain-file API key stash with owner-only permissions. Env var wins."""
from __future__ import annotations
import os, sys
from typing import Literal, Mapping
from .config import Paths

ENV_KEY = "RUNWARE_API_KEY"


def write_api_key(paths: Paths, key: str) -> None:
    key = (key or "").strip()
    if not key:
        raise ValueError("API key is empty")
    paths.secrets.mkdir(parents=True, exist_ok=True)
    fd = os.open(paths.api_key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    if sys.platform != "win32":
        os.chmod(paths.api_key_file, 0o600)


def read_api_key(paths: Paths) -> str | None:
    try:
        v = paths.api_key_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return v or None


def clear_api_key(paths: Paths) -> None:
    try:
        paths.api_key_file.unlink()
    except FileNotFoundError:
        pass


def effective_api_key(paths: Paths, env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    v = (env.get(ENV_KEY) or "").strip()
    return v or read_api_key(paths)


def key_source(paths: Paths, env: Mapping[str, str] | None = None) -> Literal["env", "file", "none"]:
    env = os.environ if env is None else env
    if (env.get(ENV_KEY) or "").strip():
        return "env"
    return "file" if read_api_key(paths) else "none"


def mask(key: str | None) -> str:
    if not key:
        return ""
    tail = key[-4:] if len(key) >= 8 else ""
    return "••••••••" + tail
```

- [ ] **Step 4: Run tests** → `uv run pytest tests/test_secrets.py -q` → `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add runwarestudio/secrets.py tests/test_secrets.py
git commit -m "feat: owner-only API key stash with env override

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Database engine, models and Alembic initial migration

**Files:**
- Create: `runwarestudio/db.py`, `runwarestudio/models/__init__.py`, `runwarestudio/models/base.py`, `runwarestudio/models/project.py`, `runwarestudio/models/prompt.py`, `runwarestudio/models/catalog.py`, `runwarestudio/models/asset.py`, `runwarestudio/models/job.py`, `runwarestudio/models/output.py`, `runwarestudio/models/setting.py`, `runwarestudio/models/usage.py`, `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_initial.py`, `runwarestudio/services/__init__.py`, `runwarestudio/services/migrate.py`, `tests/test_migrations.py`

**Interfaces:**
- Produces:
  - `db.make_engine(db_path: Path) -> Engine` (sqlite, WAL, `PRAGMA foreign_keys=ON`, `check_same_thread=False`)
  - `db.make_session_factory(engine) -> sessionmaker[Session]`
  - `db.session_scope(factory) -> contextmanager[Session]` (commit / rollback / close)
  - `models.Base` (DeclarativeBase), `models.utcnow()`; classes `Project, Prompt, CatalogModel, Asset, Job, JobStatus, Output, Setting, AppMeta, UsageEntry`
  - `services.migrate`: `alembic_config(db_path) -> Config`, `head() -> str`, `current(db_path) -> str | None`, `needs_upgrade(db_path) -> bool`, `upgrade(db_path) -> None` raising `MigrationFailed`, `SCHEMA_FAIL_MSG`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_migrations.py
import sqlite3
from pathlib import Path
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from runwarestudio import db, models
from runwarestudio.services import migrate

def test_fresh_db_upgrades_to_head(tmp_path):
    p = tmp_path / "studio.db"
    assert migrate.current(p) is None
    assert migrate.needs_upgrade(p)
    migrate.upgrade(p)
    assert migrate.current(p) == migrate.head()
    assert not migrate.needs_upgrade(p)
    names = {r[0] for r in sqlite3.connect(p).execute(
        "select name from sqlite_master where type='table'")}
    assert {"projects", "prompts", "catalog_models", "assets", "jobs", "outputs",
            "settings", "app_meta", "usage_entries", "alembic_version"} <= names

def test_models_match_migrations(tmp_path):
    p = tmp_path / "studio.db"
    migrate.upgrade(p)
    engine = db.make_engine(p)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "render_as_batch": True})
        diff = compare_metadata(ctx, models.Base.metadata)
    assert diff == [], diff

def test_engine_has_wal_and_fk(tmp_path):
    p = tmp_path / "studio.db"
    migrate.upgrade(p)
    engine = db.make_engine(p)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("pragma journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("pragma foreign_keys").scalar() == 1

def test_session_scope_commits_and_rolls_back(tmp_path):
    p = tmp_path / "studio.db"
    migrate.upgrade(p)
    factory = db.make_session_factory(db.make_engine(p))
    with db.session_scope(factory) as s:
        s.add(models.Project(name="A", slug="a"))
    try:
        with db.session_scope(factory) as s:
            s.add(models.Project(name="B", slug="b"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db.session_scope(factory) as s:
        assert [x.slug for x in s.query(models.Project).order_by(models.Project.slug)] == ["a"]
```

- [ ] **Step 2: Run to verify failure** → `uv run pytest tests/test_migrations.py -q` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement db.py**

```python
# runwarestudio/db.py
"""SQLite engine/session helpers. Sync SQLAlchemy; sessions are short-lived."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(db_path: Path) -> Engine:
    url = "sqlite:///" + str(db_path)
    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]):
    s = factory()
    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()
```

- [ ] **Step 4: Implement models**

```python
# runwarestudio/models/base.py
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
```

```python
# runwarestudio/models/project.py
from __future__ import annotations
from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    default_image_model: Mapped[str | None] = mapped_column(String(120))
    default_video_model: Mapped[str | None] = mapped_column(String(120))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (Index("ux_projects_slug", "slug", unique=True),)
```

```python
# runwarestudio/models/prompt.py
from __future__ import annotations
from datetime import datetime
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Prompt(TimestampMixin, Base):
    __tablename__ = "prompts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), default="image", nullable=False)
    form_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    composed_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    final_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    polish_json: Mapped[dict | None] = mapped_column(JSON)
    final_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_prompts_project", "project_id"), Index("ix_prompts_fav", "is_favourite"))
```

```python
# runwarestudio/models/catalog.py
from __future__ import annotations
from datetime import datetime
from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class CatalogModel(TimestampMixin, Base):
    __tablename__ = "catalog_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    air: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # image | video | text
    creator: Mapped[str | None] = mapped_column(String(80))
    architecture: Mapped[str | None] = mapped_column(String(80))
    capabilities_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    default_width: Mapped[int | None] = mapped_column(Integer)
    default_height: Mapped[int | None] = mapped_column(Integer)
    default_steps: Mapped[int | None] = mapped_column(Integer)
    default_cfg: Mapped[float | None] = mapped_column(Float)
    hero_image_url: Mapped[str | None] = mapped_column(String(500))
    price_unit: Mapped[str | None] = mapped_column(String(16))  # per_image | per_second | per_1m_tokens
    price_primary: Mapped[float | None] = mapped_column(Float)
    price_in: Mapped[float | None] = mapped_column(Float)
    price_out: Mapped[float | None] = mapped_column(Float)
    price_tiers_json: Mapped[dict | None] = mapped_column(JSON)
    price_source: Mapped[str | None] = mapped_column(String(16))
    price_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    provider_settings_schema: Mapped[list | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(10), default="curated", nullable=False)
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ux_catalog_air", "air", unique=True), Index("ix_catalog_kind", "kind"))
```

```python
# runwarestudio/models/asset.py
from __future__ import annotations
from datetime import datetime
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    mime: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    tags: Mapped[str] = mapped_column(String(500), default=",", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    media_uuid: Mapped[str | None] = mapped_column(String(64))
    media_url: Mapped[str | None] = mapped_column(String(500))
    media_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ux_assets_sha256", "sha256", unique=True),
                      Index("ux_assets_filename", "filename", unique=True),
                      Index("ix_assets_kind", "kind"))
```

```python
# runwarestudio/models/job.py
from __future__ import annotations
import enum
from datetime import datetime
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, utcnow


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default=JobStatus.queued.value, nullable=False)
    model_air: Mapped[str] = mapped_column(String(120), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    task_json: Mapped[dict | None] = mapped_column(JSON)
    dropped_params_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status_text: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text)
    cost: Mapped[float | None] = mapped_column(Float)
    runware_task_uuid: Mapped[str | None] = mapped_column(String(64))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_jobs_status", "status"),
                      Index("ix_jobs_project_created", "project_id", "created_at"),
                      Index("ix_jobs_model", "model_air"))
```

```python
# runwarestudio/models/output.py
from __future__ import annotations
from datetime import datetime
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, utcnow


class Output(Base):
    __tablename__ = "outputs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    filename: Mapped[str] = mapped_column(String(80), nullable=False)
    rel_path: Mapped[str] = mapped_column(String(300), nullable=False)
    sidecar_rel_path: Mapped[str] = mapped_column(String(300), nullable=False)
    model_air: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    params_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    cost: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str | None] = mapped_column(String(600))
    file_size: Mapped[int | None] = mapped_column(Integer)
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_missing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (Index("ix_outputs_project_created", "project_id", "created_at"),
                      Index("ix_outputs_model", "model_air"),
                      Index("ix_outputs_fav", "is_favourite"),
                      Index("ix_outputs_job", "job_id"),
                      Index("ux_outputs_project_filename", "project_id", "filename", unique=True))
```

```python
# runwarestudio/models/setting.py
from __future__ import annotations
from datetime import datetime
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, utcnow


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AppMeta(Base):
    __tablename__ = "app_meta"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
```

```python
# runwarestudio/models/usage.py
from __future__ import annotations
from datetime import date, datetime
from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, utcnow


class UsageEntry(Base):
    __tablename__ = "usage_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    task_type: Mapped[str] = mapped_column(String(30), nullable=False)
    model_air: Mapped[str | None] = mapped_column(String(120))
    cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (Index("ix_usage_project_day", "project_id", "day"), Index("ix_usage_day", "day"))
```

```python
# runwarestudio/models/__init__.py
"""Import every model so Base.metadata is complete for Alembic."""
from .base import Base, utcnow
from .project import Project
from .prompt import Prompt
from .catalog import CatalogModel
from .asset import Asset
from .job import Job, JobStatus
from .output import Output
from .setting import AppMeta, Setting
from .usage import UsageEntry

__all__ = ["Base", "utcnow", "Project", "Prompt", "CatalogModel", "Asset", "Job", "JobStatus",
           "Output", "AppMeta", "Setting", "UsageEntry"]
```

- [ ] **Step 5: Alembic wiring**

```ini
# alembic.ini
[alembic]
script_location = migrations
file_template = %%(rev)s_%%(slug)s
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
qualname =
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

```python
# migrations/env.py
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from runwarestudio.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`migrations/script.py.mako`: copy verbatim from `uv run alembic init /tmp/x` output (the standard template with `revision`, `down_revision`, `upgrade()`, `downgrade()`).

```python
# runwarestudio/services/migrate.py
"""Alembic driver. A failed upgrade raises; refusing to start beats a half-migrated DB."""
from __future__ import annotations
from pathlib import Path
import sqlite3
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from ..config import REPO_ROOT

MIGRATIONS_DIR = REPO_ROOT / "migrations"
SCHEMA_FAIL_MSG = ("Database schema migration failed. RunwareStudio will not start on an "
                   "inconsistent database. Restore the pre-migrate backup from data/backups/ if needed.")


class MigrationFailed(RuntimeError):
    pass


def alembic_config(db_path: Path | str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + str(db_path).replace("%", "%%"))
    return cfg


def head() -> str:
    return ScriptDirectory.from_config(alembic_config(":memory:")).get_current_head() or ""


def current(db_path: Path) -> str | None:
    if not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def needs_upgrade(db_path: Path) -> bool:
    return current(db_path) != head()


def upgrade(db_path: Path, revision: str = "head") -> None:
    try:
        command.upgrade(alembic_config(db_path), revision)
    except Exception as e:  # noqa: BLE001
        raise MigrationFailed(SCHEMA_FAIL_MSG) from e
```

- [ ] **Step 6: Generate the initial migration from the models, then review it**

Run:
```bash
uv run alembic -c alembic.ini -x db=ignored revision --autogenerate -m "initial" --rev-id 0001
```
Before that command works, `migrations/env.py` needs a URL: temporarily run with `ALEMBIC_URL` — simplest: run the autogenerate through Python so the URL is injected:
```bash
uv run python -c "
from alembic import command
from runwarestudio.services.migrate import alembic_config
command.revision(alembic_config('/tmp/rs_autogen.db'), message='initial', autogenerate=True, rev_id='0001')
"
rm -f /tmp/rs_autogen.db
```
Open `migrations/versions/0001_initial.py`, confirm every table and index from the models is present, and that `down_revision = None`. Remove nothing else.

- [ ] **Step 7: Run tests** → `uv run pytest tests/test_migrations.py -q` → `4 passed`. If `test_models_match_migrations` reports a diff, fix the migration file (not the model) until the diff is empty.

- [ ] **Step 8: Commit**

```bash
git add alembic.ini migrations runwarestudio/db.py runwarestudio/models runwarestudio/services tests/test_migrations.py
git commit -m "feat: SQLAlchemy models, Alembic initial schema and migrate service

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Database backups with rotation

**Files:**
- Create: `runwarestudio/services/backup.py`, `tests/test_backup.py`

**Interfaces:**
- Consumes: `config.Paths`.
- Produces: `backup_db(paths: Paths, label: str) -> Path` (filename `studio-YYYYMMDD-HHMMSS-<label>.db`, uses sqlite3 backup API, raises `FileNotFoundError` if no DB), `rotate(paths, label, keep=10) -> list[Path]` (returns deleted), `list_backups(paths) -> list[BackupInfo(path, label, created_at, size_bytes)]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backup.py
import sqlite3, time, pytest
from runwarestudio import config
from runwarestudio.services import backup, migrate

@pytest.fixture
def paths(tmp_path):
    p = config.resolve_paths(env={"RUNWARESTUDIO_DATA_DIR": str(tmp_path)})
    config.ensure_dirs(p)
    migrate.upgrade(p.db)
    return p

def test_backup_is_consistent_copy(paths):
    sqlite3.connect(paths.db).execute("insert into projects(name,slug,is_archived,created_at,updated_at) "
                                      "values('A','a',0,'2026-01-01','2026-01-01')").connection.commit()
    out = backup.backup_db(paths, "manual")
    assert out.parent == paths.backups and out.name.startswith("studio-") and out.name.endswith("-manual.db")
    assert sqlite3.connect(out).execute("select count(*) from projects").fetchone()[0] == 1

def test_backup_without_db_raises(tmp_path):
    p = config.resolve_paths(env={"RUNWARESTUDIO_DATA_DIR": str(tmp_path / "x")})
    config.ensure_dirs(p)
    with pytest.raises(FileNotFoundError):
        backup.backup_db(p, "manual")

def test_rotate_keeps_newest_per_label(paths):
    made = []
    for i in range(4):
        f = paths.backups / f"studio-2026010{i}-000000-manual.db"
        f.write_bytes(b"x"); made.append(f)
    (paths.backups / "studio-20260101-000000-pre-update.db").write_bytes(b"y")
    deleted = backup.rotate(paths, "manual", keep=2)
    assert sorted(d.name for d in deleted) == [made[0].name, made[1].name]
    assert (paths.backups / "studio-20260101-000000-pre-update.db").exists()
    infos = backup.list_backups(paths)
    assert {i.label for i in infos} == {"manual", "pre-update"}
```

- [ ] **Step 2: Run to verify failure** → `uv run pytest tests/test_backup.py -q` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# runwarestudio/services/backup.py
"""SQLite backups via the online backup API (safe under WAL)."""
from __future__ import annotations
import re, sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from ..config import Paths

_NAME = re.compile(r"^studio-(\d{8}-\d{6})-(.+)\.db$")


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    label: str
    created_at: datetime
    size_bytes: int


def backup_db(paths: Paths, label: str) -> Path:
    if not paths.db.exists():
        raise FileNotFoundError(paths.db)
    paths.backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = paths.backups / f"studio-{stamp}-{label}.db"
    src = sqlite3.connect(paths.db)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()
    return dest


def list_backups(paths: Paths) -> list[BackupInfo]:
    out: list[BackupInfo] = []
    if not paths.backups.exists():
        return out
    for f in paths.backups.glob("studio-*.db"):
        m = _NAME.match(f.name)
        if not m:
            continue
        out.append(BackupInfo(f, m.group(2), datetime.strptime(m.group(1), "%Y%m%d-%H%M%S"), f.stat().st_size))
    return sorted(out, key=lambda b: b.created_at, reverse=True)


def rotate(paths: Paths, label: str, keep: int = 10) -> list[Path]:
    same = [b for b in list_backups(paths) if b.label == label]
    deleted = []
    for b in same[keep:]:
        b.path.unlink(missing_ok=True)
        deleted.append(b.path)
    return deleted
```

- [ ] **Step 4: Run tests** → `3 passed`.
- [ ] **Step 5: Commit** → `git add runwarestudio/services/backup.py tests/test_backup.py && git commit -m "feat: sqlite backups with per-label rotation" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 6: Settings service, app_meta helpers and git info

**Files:**
- Create: `runwarestudio/services/settings.py`, `runwarestudio/services/meta.py`, `runwarestudio/services/gitinfo.py`, `tests/test_settings.py`, `tests/test_gitinfo.py`

**Interfaces:**
- Produces:
  - `settings.SPEC: dict[str, Spec]` with `Spec(type: type, default, choices: tuple | None, env: str | None)`. Keys: `runware.transport` (str, "rest", choices rest/websocket), `runware.timeout_s` (int, 1200), `jobs.concurrency` (int, 3), `paths.outputs_dir` (str, ""), `defaults.image_model` (str, "runware:101@1"), `defaults.video_model` (str, "lightricks:ltx@2.3"), `defaults.polish_model` (str, ""), `defaults.output_format_image` (str, "PNG"), `defaults.output_format_video` (str, "MP4"), `defaults.negative_prompt` (str, "blurry, low quality, watermark, text, deformed"), `prompt.polish_mode` (str, "promptEnhance"), `ui.theme` (str, "dark", choices dark/light), `uploads.max_mb` (int, 200).
  - `settings.get(session, key, env=None)`; `settings.set_many(session, values: dict[str, str]) -> None` (validates type/choices, raises `ValueError`); `settings.all_values(session, env=None) -> dict`.
  - `meta.get(session, key) -> str | None`, `meta.set(session, key, value: str) -> None`.
  - `gitinfo.is_git_install() -> bool`, `gitinfo.current_commit() -> CommitInfo(sha: str, short: str, subject: str) | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_settings.py
import pytest
from runwarestudio import db, models
from runwarestudio.services import migrate, settings, meta

@pytest.fixture
def session(tmp_path):
    p = tmp_path / "s.db"; migrate.upgrade(p)
    factory = db.make_session_factory(db.make_engine(p))
    with db.session_scope(factory) as s:
        yield s

def test_defaults(session):
    assert settings.get(session, "jobs.concurrency") == 3
    assert settings.get(session, "ui.theme") == "dark"

def test_set_and_get_casts(session):
    settings.set_many(session, {"jobs.concurrency": "5", "ui.theme": "light"})
    assert settings.get(session, "jobs.concurrency") == 5
    assert settings.get(session, "ui.theme") == "light"

def test_invalid_values_rejected(session):
    with pytest.raises(ValueError): settings.set_many(session, {"jobs.concurrency": "x"})
    with pytest.raises(ValueError): settings.set_many(session, {"ui.theme": "sepia"})
    with pytest.raises(ValueError): settings.set_many(session, {"nope": "1"})

def test_env_precedence(session):
    settings.set_many(session, {"runware.transport": "websocket"})
    assert settings.get(session, "runware.transport", env={"RUNWARESTUDIO_TRANSPORT": "rest"}) == "rest"

def test_meta_round_trip(session):
    assert meta.get(session, "x") is None
    meta.set(session, "x", "1"); meta.set(session, "x", "2")
    assert meta.get(session, "x") == "2"
```

```python
# tests/test_gitinfo.py
from runwarestudio.services import gitinfo

def test_git_install_detected():
    assert gitinfo.is_git_install() is True
    c = gitinfo.current_commit()
    assert c is not None and len(c.short) >= 7 and c.subject
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# runwarestudio/services/settings.py
"""Typed key/value settings. Precedence: env > settings table > SPEC default."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any, Mapping
from sqlalchemy.orm import Session
from ..models import Setting


@dataclass(frozen=True)
class Spec:
    type: type
    default: Any
    choices: tuple[str, ...] | None = None
    env: str | None = None


SPEC: dict[str, Spec] = {
    "runware.transport": Spec(str, "rest", ("rest", "websocket"), "RUNWARESTUDIO_TRANSPORT"),
    "runware.timeout_s": Spec(int, 1200),
    "jobs.concurrency": Spec(int, 3),
    "paths.outputs_dir": Spec(str, ""),
    "defaults.image_model": Spec(str, "runware:101@1"),
    "defaults.video_model": Spec(str, "lightricks:ltx@2.3"),
    "defaults.polish_model": Spec(str, ""),
    "defaults.output_format_image": Spec(str, "PNG", ("PNG", "JPG", "WEBP")),
    "defaults.output_format_video": Spec(str, "MP4", ("MP4", "WEBM")),
    "defaults.negative_prompt": Spec(str, "blurry, low quality, watermark, text, deformed"),
    "prompt.polish_mode": Spec(str, "promptEnhance", ("promptEnhance", "textInference")),
    "ui.theme": Spec(str, "dark", ("dark", "light")),
    "uploads.max_mb": Spec(int, 200),
}


def _cast(key: str, raw: str) -> Any:
    spec = SPEC[key]
    if spec.type is int:
        try:
            return int(raw)
        except ValueError as e:
            raise ValueError(f"{key} must be an integer") from e
    if spec.type is bool:
        return raw.lower() in ("1", "true", "yes", "on")
    if spec.choices and raw not in spec.choices:
        raise ValueError(f"{key} must be one of {', '.join(spec.choices)}")
    return raw


def get(session: Session, key: str, env: Mapping[str, str] | None = None) -> Any:
    spec = SPEC[key]
    env = os.environ if env is None else env
    if spec.env and env.get(spec.env):
        return _cast(key, env[spec.env])
    row = session.get(Setting, key)
    return _cast(key, row.value) if row else spec.default


def set_many(session: Session, values: Mapping[str, str]) -> None:
    for key, raw in values.items():
        if key not in SPEC:
            raise ValueError(f"unknown setting {key}")
        _cast(key, raw)
    for key, raw in values.items():
        row = session.get(Setting, key)
        if row:
            row.value = raw
        else:
            session.add(Setting(key=key, value=raw))
    session.flush()


def all_values(session: Session, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return {k: get(session, k, env) for k in SPEC}
```

```python
# runwarestudio/services/meta.py
from __future__ import annotations
from sqlalchemy.orm import Session
from ..models import AppMeta


def get(session: Session, key: str) -> str | None:
    row = session.get(AppMeta, key)
    return row.value if row else None


def set(session: Session, key: str, value: str) -> None:  # noqa: A001
    row = session.get(AppMeta, key)
    if row:
        row.value = value
    else:
        session.add(AppMeta(key=key, value=value))
    session.flush()
```

```python
# runwarestudio/services/gitinfo.py
"""Read-only git facts about the checkout. Never prompts, never fails loudly."""
from __future__ import annotations
import os, subprocess
from dataclasses import dataclass
from ..config import REPO_ROOT

GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -oBatchMode=yes")


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    short: str
    subject: str


def git_bin() -> str:
    return os.environ.get("RUNWARESTUDIO_GIT") or "git"


def run_git(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run([git_bin(), *args], cwd=REPO_ROOT, capture_output=True, text=True,
                          timeout=timeout, env=GIT_ENV, check=False)


def is_git_install() -> bool:
    return (REPO_ROOT / ".git").exists()


def current_commit() -> CommitInfo | None:
    if not is_git_install():
        return None
    try:
        p = run_git(["log", "-1", "--format=%H%x00%h%x00%s"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    sha, short, subject = p.stdout.strip().split("\x00", 2)
    return CommitInfo(sha, short, subject)
```

- [ ] **Step 4: Run tests** → `uv run pytest tests/test_settings.py tests/test_gitinfo.py -q` → `6 passed`.
- [ ] **Step 5: Commit** → `git add runwarestudio/services/settings.py runwarestudio/services/meta.py runwarestudio/services/gitinfo.py tests/test_settings.py tests/test_gitinfo.py && git commit -m "feat: typed settings, app_meta and git info services" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 7: Boot sequence

**Files:**
- Create: `runwarestudio/boot.py`, `tests/test_boot.py`

**Interfaces:**
- Consumes: `config.ensure_dirs`, `migrate.*`, `backup.backup_db/rotate`, `meta.set`, `gitinfo.current_commit`, models.
- Produces: `@dataclass BootInfo(paths, engine, session_factory, schema_revision: str, version: str, commit: CommitInfo | None, boot_id: str, started_at: datetime, orphaned_jobs: int, requeued_jobs: list[str])` and `def boot(paths: Paths) -> BootInfo` (raises `MigrationFailed`). Also `def orphan_jobs(session) -> tuple[int, list[str]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_boot.py
import pytest
from runwarestudio import boot, config, db, models
from runwarestudio.services import backup, migrate

@pytest.fixture
def paths(tmp_path):
    return config.resolve_paths(env={"RUNWARESTUDIO_DATA_DIR": str(tmp_path)})

def test_first_boot_creates_schema_default_project_and_meta(paths):
    info = boot.boot(paths)
    assert info.schema_revision == migrate.head()
    assert paths.db.exists() and paths.outputs.is_dir()
    with db.session_scope(info.session_factory) as s:
        assert s.query(models.Project).filter_by(slug="default").one().name == "Default"
        assert s.get(models.AppMeta, "schema_revision").value == migrate.head()
        assert s.get(models.AppMeta, "first_boot_at") is not None
    assert backup.list_backups(paths) == []  # fresh DB: nothing to back up

def test_orphan_and_requeue(paths):
    info = boot.boot(paths)
    with db.session_scope(info.session_factory) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
        s.add(models.Job(id="run1", project_id=pid, kind="image", status="running", model_air="m", request_json={}))
        s.add(models.Job(id="q1", project_id=pid, kind="image", status="queued", model_air="m", request_json={}))
    info2 = boot.boot(paths)
    assert info2.orphaned_jobs == 1 and info2.requeued_jobs == ["q1"]
    with db.session_scope(info2.session_factory) as s:
        j = s.get(models.Job, "run1")
        assert j.status == "failed" and j.error_code == "orphaned"

def test_backup_taken_when_schema_behind(paths, monkeypatch):
    boot.boot(paths)
    monkeypatch.setattr(migrate, "needs_upgrade", lambda p: True)
    monkeypatch.setattr(migrate, "upgrade", lambda p, revision="head": None)
    boot.boot(paths)
    assert [b.label for b in backup.list_backups(paths)] == ["pre-migrate"]

def test_migration_failure_propagates(paths, monkeypatch):
    def bad(p, revision="head"):
        raise migrate.MigrationFailed("nope")
    monkeypatch.setattr(migrate, "upgrade", bad)
    with pytest.raises(migrate.MigrationFailed):
        boot.boot(paths)
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# runwarestudio/boot.py
"""Boot: dirs -> backup-if-migrating -> upgrade -> meta -> default project -> orphan jobs."""
from __future__ import annotations
import logging, uuid
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from . import __version__, config, db
from .models import Job, JobStatus, Project, utcnow
from .services import backup, gitinfo, meta, migrate
from .services.gitinfo import CommitInfo

log = logging.getLogger(__name__)


@dataclass
class BootInfo:
    paths: config.Paths
    engine: Engine
    session_factory: sessionmaker[Session]
    schema_revision: str
    version: str
    commit: CommitInfo | None
    boot_id: str
    started_at: datetime
    orphaned_jobs: int
    requeued_jobs: list[str]


def orphan_jobs(session: Session) -> tuple[int, list[str]]:
    running = session.query(Job).filter(Job.status == JobStatus.running.value).all()
    for j in running:
        j.status = JobStatus.failed.value
        j.error_code = "orphaned"
        j.error_message = "Server restarted while this job was running."
        j.finished_at = utcnow()
    queued = [j.id for j in session.query(Job).filter(Job.status == JobStatus.queued.value)
              .order_by(Job.created_at).all()]
    session.flush()
    return len(running), queued


def boot(paths: config.Paths) -> BootInfo:
    config.ensure_dirs(paths)
    if paths.db.exists() and migrate.needs_upgrade(paths.db):
        b = backup.backup_db(paths, "pre-migrate")
        backup.rotate(paths, "pre-migrate")
        log.info("pre-migrate backup: %s", b.name)
    migrate.upgrade(paths.db)  # raises MigrationFailed
    engine = db.make_engine(paths.db)
    factory = db.make_session_factory(engine)
    commit = gitinfo.current_commit()
    now = utcnow()
    with db.session_scope(factory) as s:
        meta.set(s, "schema_revision", migrate.head())
        meta.set(s, "app_version_last_boot", __version__)
        meta.set(s, "git_commit_last_boot", commit.sha if commit else "")
        meta.set(s, "last_boot_at", now.isoformat())
        if meta.get(s, "first_boot_at") is None:
            meta.set(s, "first_boot_at", now.isoformat())
        if not s.query(Project).filter_by(slug="default").first():
            s.add(Project(name="Default", slug="default"))
            (paths.outputs / "default").mkdir(parents=True, exist_ok=True)
        orphaned, requeued = orphan_jobs(s)
    return BootInfo(paths, engine, factory, migrate.head(), __version__, commit,
                    uuid.uuid4().hex, now, orphaned, requeued)
```

- [ ] **Step 4: Run tests** → `uv run pytest tests/test_boot.py -q` → `4 passed`.
- [ ] **Step 5: Commit** → `git add runwarestudio/boot.py tests/test_boot.py && git commit -m "feat: boot sequence with pre-migrate backup and orphan handling" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 8: RunWare client factory, error mapping, account balance, and the test fake

**Files:**
- Create: `runwarestudio/runware/__init__.py`, `runwarestudio/runware/client.py`, `runwarestudio/runware/errors.py`, `runwarestudio/services/account.py`, `tests/fakes/__init__.py`, `tests/fakes/fake_runware.py`, `tests/test_errors.py`, `tests/test_account.py`

**Interfaces:**
- Produces:
  - `client.ClientFactory = Callable[[str, str], AbstractAsyncContextManager[Any]]`; `client.open_client(api_key: str, transport: str = "rest")` → async context manager yielding a connected `runware.Runware`.
  - `errors.UserFacingError(code: str, message: str, retryable: bool)`; `errors.classify(exc: BaseException) -> UserFacingError` (handles `runware.RunwareError` by `.code`, plus any other exception → code `unknown`).
  - `account.BalanceInfo(amount: float, currency: str, free: float, fetched_at: datetime)`; `async account.refresh_balance(client_factory, api_key, transport, session_factory) -> BalanceInfo` (calls `account_management({"operation":"getDetails"})`, stores `account.balance`, `account.balance_at`, `account.currency` in app_meta); `account.cached_balance(session) -> BalanceInfo | None`.
  - Test fake `FakeRunware(script: dict[str, list])` with `async run(task, options=None)`, `async account_management(params, options=None)`, `async model_search(...)`, `async media_storage(...)`, `.calls: list[tuple[str, dict]]`, and `fake_factory(fake) -> ClientFactory`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/fakes/fake_runware.py
"""Scripted stand-in for runware.Runware. Each method pops the next scripted reply for its
name; a reply that is an Exception is raised instead of returned."""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any


class FakeRunware:
    def __init__(self, script: dict[str, list[Any]] | None = None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[tuple[str, dict]] = []

    def _reply(self, name: str, params: dict) -> Any:
        self.calls.append((name, params))
        queue = self.script.get(name) or []
        if not queue:
            raise AssertionError(f"FakeRunware has no scripted reply for {name}")
        r = queue.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    async def run(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("run", params)

    async def account_management(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("account_management", params)

    async def model_search(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("model_search", params)

    async def media_storage(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("media_storage", params)


def fake_factory(fake: FakeRunware):
    @asynccontextmanager
    async def _open(api_key: str, transport: str = "rest"):
        fake.calls.append(("open", {"api_key_len": len(api_key), "transport": transport}))
        yield fake
    return _open
```

```python
# tests/test_errors.py
from runware import RunwareError
from runwarestudio.runware import errors

def test_known_code_maps_to_message():
    e = errors.classify(RunwareError("invalidApiKey", "Invalid API key"))
    assert e.code == "auth" and "Settings" in e.message and e.retryable is False

def test_rate_limit_is_retryable():
    e = errors.classify(RunwareError("rateLimitExceeded", "slow down"))
    assert e.code == "rateLimit" and e.retryable is True

def test_unknown_exception():
    e = errors.classify(ValueError("boom"))
    assert e.code == "unknown" and "boom" in e.message
```

```python
# tests/test_account.py
import pytest
from runware import RunwareError
from runwarestudio import db
from runwarestudio.services import account, migrate
from tests.fakes.fake_runware import FakeRunware, fake_factory

@pytest.fixture
def factory(tmp_path):
    p = tmp_path / "s.db"; migrate.upgrade(p)
    return db.make_session_factory(db.make_engine(p))

async def test_refresh_balance_stores_meta(factory):
    fake = FakeRunware({"account_management": [[{"balance": {"amount": 12.5, "freeBalance": 0.0, "currency": "USD"}}]]})
    info = await account.refresh_balance(fake_factory(fake), "key", "rest", factory)
    assert info.amount == 12.5 and info.currency == "USD"
    assert fake.calls[-1] == ("account_management", {"operation": "getDetails"})
    with db.session_scope(factory) as s:
        cached = account.cached_balance(s)
    assert cached is not None and cached.amount == 12.5

async def test_refresh_balance_raises_user_facing(factory):
    fake = FakeRunware({"account_management": [RunwareError("invalidApiKey", "bad")]})
    with pytest.raises(account.BalanceError) as ei:
        await account.refresh_balance(fake_factory(fake), "key", "rest", factory)
    assert ei.value.error.code == "auth"

def test_cached_balance_none_when_unset(factory):
    with db.session_scope(factory) as s:
        assert account.cached_balance(s) is None
```

- [ ] **Step 2: Run to verify failure** → `uv run pytest tests/test_errors.py tests/test_account.py -q` → import errors.

- [ ] **Step 3: Implement**

```python
# runwarestudio/runware/__init__.py
"""Thin adapter over the official runware-sdk. Never imports runwarestudio.web."""
```

```python
# runwarestudio/runware/client.py
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, AsyncContextManager
from runware import Runware

ClientFactory = Callable[..., AsyncContextManager[Any]]


@asynccontextmanager
async def open_client(api_key: str, transport: str = "rest") -> AsyncIterator[Runware]:
    """One connected client. REST needs no connect; websocket connects lazily on first run()."""
    client = Runware(api_key=api_key, transport=transport)
    try:
        yield client
    finally:
        await client.close()
```

```python
# runwarestudio/runware/errors.py
from __future__ import annotations
from dataclasses import dataclass
from runware import RunwareError

_MESSAGES: dict[str, tuple[str, bool]] = {
    "validation": ("The model rejected a parameter: {detail}", False),
    "auth": ("RunWare rejected the API key. Check it in Settings.", False),
    "quota": ("RunWare account balance or quota exhausted. Top up at my.runware.ai.", False),
    "rateLimit": ("Rate limited by RunWare. Retrying.", True),
    "safety": ("Blocked by the content safety filter. Adjust the prompt.", False),
    "provider": ("The model provider failed: {detail}", False),
    "timeout": ("Timed out waiting for RunWare.", False),
    "notFound": ("Model or media not found: {detail}", False),
    "serverError": ("RunWare server error. Retrying once.", True),
    "connection": ("Could not reach RunWare. Check your connection.", True),
    "aborted": ("Cancelled.", False),
    "unknown": ("Unexpected error: {detail}", False),
}


@dataclass(frozen=True)
class UserFacingError:
    code: str
    message: str
    retryable: bool
    parameter: str | None = None


def classify(exc: BaseException) -> UserFacingError:
    if isinstance(exc, RunwareError):
        code = exc.code if exc.code in _MESSAGES else "unknown"
        tmpl, retry = _MESSAGES[code]
        return UserFacingError(code, tmpl.format(detail=exc.message), retry,
                               getattr(exc, "parameter", None))
    tmpl, retry = _MESSAGES["unknown"]
    return UserFacingError("unknown", tmpl.format(detail=str(exc) or exc.__class__.__name__), retry)
```

```python
# runwarestudio/services/account.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.orm import Session, sessionmaker
from .. import db
from ..models import utcnow
from ..runware.errors import UserFacingError, classify
from . import meta


@dataclass(frozen=True)
class BalanceInfo:
    amount: float
    currency: str
    free: float
    fetched_at: datetime


class BalanceError(Exception):
    def __init__(self, error: UserFacingError):
        super().__init__(error.message)
        self.error = error


async def refresh_balance(client_factory, api_key: str, transport: str,
                          session_factory: sessionmaker[Session]) -> BalanceInfo:
    try:
        async with client_factory(api_key, transport) as client:
            rows = await client.account_management({"operation": "getDetails"})
    except Exception as e:  # noqa: BLE001
        raise BalanceError(classify(e)) from e
    bal = (rows[0] if rows else {}).get("balance") or {}
    info = BalanceInfo(float(bal.get("amount", 0.0)), str(bal.get("currency", "USD")),
                       float(bal.get("freeBalance", 0.0)), utcnow())
    with db.session_scope(session_factory) as s:
        meta.set(s, "account.balance", repr(info.amount))
        meta.set(s, "account.free", repr(info.free))
        meta.set(s, "account.currency", info.currency)
        meta.set(s, "account.balance_at", info.fetched_at.isoformat())
    return info


def cached_balance(session: Session) -> BalanceInfo | None:
    amount, at = meta.get(session, "account.balance"), meta.get(session, "account.balance_at")
    if amount is None or at is None:
        return None
    return BalanceInfo(float(amount), meta.get(session, "account.currency") or "USD",
                       float(meta.get(session, "account.free") or 0.0), datetime.fromisoformat(at))
```

`tests/fakes/__init__.py` is empty.

- [ ] **Step 4: Run tests** → `uv run pytest tests/test_errors.py tests/test_account.py -q` → `6 passed`. If `RunwareError(raw_code, message)` positional order differs in the installed SDK, check `uv run python -c "import inspect, runware; print(inspect.signature(runware.RunwareError))"` and adjust the tests' constructor calls, not the app code.
- [ ] **Step 5: Commit** → `git add runwarestudio/runware runwarestudio/services/account.py tests/fakes tests/test_errors.py tests/test_account.py && git commit -m "feat: runware client factory, error mapping, balance service, test fake" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 9: FastAPI app, health endpoint, base layout, Settings page

**Files:**
- Create: `runwarestudio/web/__init__.py`, `runwarestudio/web/app.py`, `runwarestudio/web/deps.py`, `runwarestudio/web/routes/__init__.py`, `runwarestudio/web/routes/system.py`, `runwarestudio/web/routes/pages.py`, `runwarestudio/web/routes/settings.py`, templates and static listed below, `tests/conftest.py`, `tests/test_web_health.py`, `tests/test_web_settings.py`

**Interfaces:**
- Consumes: `boot.boot`, `settings.*`, `secrets.*`, `account.*`, `errors.classify`.
- Produces: `create_app(paths: Paths, client_factory: ClientFactory = open_client, env: Mapping | None = None) -> FastAPI`; `app.state.boot: BootInfo`, `app.state.client_factory`, `app.state.env`. Routes: `GET /api/health`, `GET /`, `GET /settings`, `POST /settings`, `POST /settings/api-key`, `POST /settings/api-key/clear`, `POST /settings/api-key/test`, `GET /hx/header/balance`. Template globals: `app_version`, `commit_short`, `theme`, `has_api_key`, `key_source`.

- [ ] **Step 1: Vendor static assets**

```bash
mkdir -p runwarestudio/web/static/vendor runwarestudio/web/static/css runwarestudio/web/static/js runwarestudio/web/static/img
curl -fsSL https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js -o runwarestudio/web/static/vendor/htmx.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js -o runwarestudio/web/static/vendor/alpine.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/@alpinejs/focus@3.14.8/dist/cdn.min.js -o runwarestudio/web/static/vendor/alpine-focus.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/@picocss/pico@2.0.6/css/pico.min.css -o runwarestudio/web/static/vendor/pico.min.css
head -c 200 runwarestudio/web/static/vendor/htmx.min.js  # sanity: JS, not an HTML error page
```
Write `runwarestudio/web/static/vendor/LICENSES.md` naming htmx (0BSD), Alpine (MIT), Pico (MIT) with versions.

- [ ] **Step 2: Write the failing tests**

```python
# tests/conftest.py
import pytest, httpx
from runwarestudio import config
from runwarestudio.web.app import create_app
from tests.fakes.fake_runware import FakeRunware, fake_factory

@pytest.fixture
def paths(tmp_path):
    return config.resolve_paths(env={"RUNWARESTUDIO_DATA_DIR": str(tmp_path / "data")})

@pytest.fixture
def fake():
    return FakeRunware()

@pytest.fixture
def app(paths, fake):
    return create_app(paths, client_factory=fake_factory(fake), env={})

@pytest.fixture
async def client(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c
```

```python
# tests/test_web_health.py
from runwarestudio import __version__

async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["app"] == "RunwareStudio" and body["version"] == __version__
    assert body["schema"] and body["boot_id"] and "port" in body

async def test_index_renders(client):
    r = await client.get("/")
    assert r.status_code == 200 and "RunwareStudio" in r.text and "No API key" in r.text
```

```python
# tests/test_web_settings.py
from runware import RunwareError
from runwarestudio import secrets

async def test_settings_page_lists_fields(client):
    r = await client.get("/settings")
    assert r.status_code == 200
    for key in ("jobs.concurrency", "ui.theme", "runware.transport"):
        assert f'name="{key}"' in r.text

async def test_save_settings(client):
    r = await client.post("/settings", data={"jobs.concurrency": "4", "ui.theme": "light",
                                              "runware.transport": "rest"})
    assert r.status_code == 200 and "Saved" in r.text
    r = await client.get("/settings")
    assert 'value="4"' in r.text

async def test_save_invalid_setting_422(client):
    r = await client.post("/settings", data={"jobs.concurrency": "many"})
    assert r.status_code == 422 and "integer" in r.text

async def test_api_key_save_masks_and_never_echoes(client, paths):
    r = await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    assert r.status_code == 200 and "••••••••1234" in r.text and "abcdefgh1234" not in r.text
    assert secrets.read_api_key(paths) == "abcdefgh1234"
    r = await client.post("/settings/api-key/clear")
    assert r.status_code == 200 and secrets.read_api_key(paths) is None

async def test_api_key_test_shows_balance(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["account_management"] = [[{"balance": {"amount": 7.25, "currency": "USD", "freeBalance": 0}}]]
    r = await client.post("/settings/api-key/test")
    assert r.status_code == 200 and "$7.25" in r.text
    r = await client.get("/hx/header/balance")
    assert "$7.25" in r.text

async def test_api_key_test_failure_422(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["account_management"] = [RunwareError("invalidApiKey", "bad")]
    r = await client.post("/settings/api-key/test")
    assert r.status_code == 422 and "rejected the API key" in r.text

async def test_api_key_test_without_key_422(client):
    r = await client.post("/settings/api-key/test")
    assert r.status_code == 422 and "No API key" in r.text
```

- [ ] **Step 3: Run to verify failure** → `uv run pytest tests/test_web_health.py tests/test_web_settings.py -q` → import errors.

- [ ] **Step 4: Implement app and deps**

```python
# runwarestudio/web/__init__.py
```

```python
# runwarestudio/web/deps.py
from __future__ import annotations
from pathlib import Path
from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def is_hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    """Render a template with the standard globals merged in."""
    app = request.app
    base = {
        "request": request,
        "app_version": app.state.boot.version,
        "commit_short": app.state.boot.commit.short if app.state.boot.commit else "",
        "theme": app.state.theme(),
        "has_api_key": app.state.api_key() is not None,
        "key_source": app.state.key_source(),
    }
    base.update(ctx or {})
    return templates.TemplateResponse(request, name, base, status_code=status_code)
```

```python
# runwarestudio/web/app.py
from __future__ import annotations
import os
from contextlib import asynccontextmanager
from typing import Mapping
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .. import boot as _boot, config, db, secrets
from ..runware.client import open_client
from ..services import settings as settings_svc
from .deps import STATIC_DIR
from .routes import pages, settings as settings_routes, system


def create_app(paths: config.Paths, client_factory=open_client, env: Mapping[str, str] | None = None,
               port: int = config.DEFAULT_PORT) -> FastAPI:
    env = os.environ if env is None else env

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.boot = _boot.boot(paths)
        yield
        app.state.boot.engine.dispose()

    app = FastAPI(title="RunwareStudio", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.paths = paths
    app.state.client_factory = client_factory
    app.state.env = env
    app.state.port = port
    app.state.api_key = lambda: secrets.effective_api_key(paths, env)
    app.state.key_source = lambda: secrets.key_source(paths, env)

    def theme() -> str:
        with db.session_scope(app.state.boot.session_factory) as s:
            return settings_svc.get(s, "ui.theme", env)

    def setting(key: str):
        with db.session_scope(app.state.boot.session_factory) as s:
            return settings_svc.get(s, key, env)

    app.state.theme = theme
    app.state.setting = setting
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(system.router)
    app.include_router(pages.router)
    app.include_router(settings_routes.router)
    return app
```

- [ ] **Step 5: Implement routes**

```python
# runwarestudio/web/routes/__init__.py
```

```python
# runwarestudio/web/routes/system.py
from __future__ import annotations
import os
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/health")
async def health(request: Request):
    b = request.app.state.boot
    return {"ok": True, "app": "RunwareStudio", "version": b.version,
            "commit": b.commit.sha if b.commit else "", "schema": b.schema_revision,
            "boot_id": b.boot_id, "started_at": b.started_at.isoformat(), "pid": os.getpid(),
            "port": request.app.state.port}
```

```python
# runwarestudio/web/routes/pages.py
from __future__ import annotations
from fastapi import APIRouter, Request
from .. import deps

router = APIRouter()


@router.get("/")
async def index(request: Request):
    return deps.render(request, "pages/index.html")
```

```python
# runwarestudio/web/routes/settings.py
from __future__ import annotations
from fastapi import APIRouter, Request
from ... import db, secrets
from ...services import account, settings as settings_svc
from .. import deps

router = APIRouter()


def _general_ctx(request: Request, saved: bool = False, error: str | None = None) -> dict:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        values = settings_svc.all_values(s, request.app.state.env)
    return {"spec": settings_svc.SPEC, "values": values, "saved": saved, "error": error}


def _key_ctx(request: Request, message: str | None = None, error: str | None = None,
             balance: account.BalanceInfo | None = None) -> dict:
    paths = request.app.state.paths
    key = request.app.state.api_key()
    return {"masked": secrets.mask(key), "source": request.app.state.key_source(),
            "message": message, "error": error, "balance": balance,
            "env_locked": request.app.state.key_source() == "env", "paths": paths}


@router.get("/settings")
async def settings_page(request: Request):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        bal = account.cached_balance(s)
    return deps.render(request, "pages/settings.html", {**_general_ctx(request), **_key_ctx(request, balance=bal)})


@router.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    values = {k: str(v) for k, v in form.items() if k in settings_svc.SPEC}
    try:
        with db.session_scope(request.app.state.boot.session_factory) as s:
            settings_svc.set_many(s, values)
    except ValueError as e:
        return deps.render(request, "settings/_general_form.html", _general_ctx(request, error=str(e)), 422)
    return deps.render(request, "settings/_general_form.html", _general_ctx(request, saved=True))


@router.post("/settings/api-key")
async def save_api_key(request: Request):
    form = await request.form()
    if request.app.state.key_source() == "env":
        return deps.render(request, "settings/_api_key_form.html",
                           _key_ctx(request, error="RUNWARE_API_KEY is set in the environment; the file is ignored."), 422)
    try:
        secrets.write_api_key(request.app.state.paths, str(form.get("api_key", "")))
    except ValueError:
        return deps.render(request, "settings/_api_key_form.html", _key_ctx(request, error="Enter a key."), 422)
    return deps.render(request, "settings/_api_key_form.html", _key_ctx(request, message="Key saved."))


@router.post("/settings/api-key/clear")
async def clear_api_key(request: Request):
    secrets.clear_api_key(request.app.state.paths)
    return deps.render(request, "settings/_api_key_form.html", _key_ctx(request, message="Key removed."))


@router.post("/settings/api-key/test")
async def test_api_key(request: Request):
    key = request.app.state.api_key()
    if not key:
        return deps.render(request, "settings/_api_key_form.html", _key_ctx(request, error="No API key set."), 422)
    try:
        bal = await account.refresh_balance(request.app.state.client_factory, key,
                                            request.app.state.setting("runware.transport"),
                                            request.app.state.boot.session_factory)
    except account.BalanceError as e:
        return deps.render(request, "settings/_api_key_form.html", _key_ctx(request, error=e.error.message), 422)
    return deps.render(request, "settings/_api_key_form.html",
                       _key_ctx(request, message="Key works.", balance=bal))


@router.get("/hx/header/balance")
async def header_balance(request: Request):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        bal = account.cached_balance(s)
    return deps.render(request, "partials/_balance_chip.html", {"balance": bal})
```

- [ ] **Step 6: Templates and static**

```html
{# runwarestudio/web/templates/base.html #}
<!doctype html>
<html lang="en" data-theme="{{ theme }}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}RunwareStudio{% endblock %}</title>
  <link rel="icon" href="/static/img/favicon.svg">
  <link rel="stylesheet" href="/static/vendor/pico.min.css">
  <link rel="stylesheet" href="/static/css/app.css">
  <script src="/static/vendor/htmx.min.js" defer></script>
  <script src="/static/js/app.js" defer></script>
  <script src="/static/vendor/alpine-focus.min.js" defer></script>
  <script src="/static/vendor/alpine.min.js" defer></script>
</head>
<body hx-boost="false">
  {% include "_header.html" %}
  <main class="container">{% block content %}{% endblock %}</main>
  <div id="toasts" role="status" aria-live="polite"></div>
  <footer class="container"><small>RunwareStudio v{{ app_version }}{% if commit_short %} · {{ commit_short }}{% endif %}</small></footer>
</body>
</html>
```

```html
{# runwarestudio/web/templates/_header.html #}
<header class="container">
  <nav>
    <ul><li><strong><a href="/">RunwareStudio</a></strong></li></ul>
    <ul>
      <li><a href="/">Home</a></li>
      <li><a href="/settings">Settings</a></li>
      {% include "partials/_balance_chip.html" %}
      <li><button class="secondary outline" type="button" onclick="rsToggleTheme()" aria-label="Toggle theme">◐</button></li>
    </ul>
  </nav>
</header>
```

```html
{# runwarestudio/web/templates/partials/_balance_chip.html #}
<li id="balance-chip" {% if oob %}hx-swap-oob="true"{% endif %} hx-get="/hx/header/balance" hx-trigger="every 60s" hx-swap="outerHTML">
  {% if not has_api_key %}<a href="/settings#api" class="chip warn">No API key</a>
  {% elif balance %}<span class="chip" title="fetched {{ balance.fetched_at }}">${{ '%.2f'|format(balance.amount) }}</span>
  {% else %}<a href="/settings#api" class="chip">Balance unknown</a>{% endif %}
</li>
```

```html
{# runwarestudio/web/templates/partials/_toast.html #}
<div id="toasts" hx-swap-oob="beforeend"><div class="toast {{ level|default('info') }}">{{ text }}</div></div>
```

```html
{# runwarestudio/web/templates/pages/index.html #}
{% extends "base.html" %}
{% block content %}
<h1>RunwareStudio</h1>
{% if not has_api_key %}<article class="warn">No API key set. <a href="/settings#api">Add your RunWare key</a> to start generating.</article>{% endif %}
<p>Generate, Gallery, Prompts and Assets arrive in the next phases.</p>
{% endblock %}
```

```html
{# runwarestudio/web/templates/pages/settings.html #}
{% extends "base.html" %}
{% block title %}Settings · RunwareStudio{% endblock %}
{% block content %}
<h1>Settings</h1>
<section id="api"><h2>RunWare API</h2>{% include "settings/_api_key_form.html" %}</section>
<section id="general"><h2>General</h2>{% include "settings/_general_form.html" %}</section>
{% endblock %}
```

```html
{# runwarestudio/web/templates/settings/_api_key_form.html #}
<form id="api-key-form" hx-post="/settings/api-key" hx-swap="outerHTML" hx-target="this">
  <label>API key
    <input type="password" name="api_key" placeholder="{{ masked or 'paste your RunWare key' }}" autocomplete="off" {% if env_locked %}disabled{% endif %}>
  </label>
  <small>Source: {{ source }}{% if masked %} · {{ masked }}{% endif %}. Stored in {{ paths.api_key_file }} (owner-only).</small>
  <div class="grid">
    <button type="submit" {% if env_locked %}disabled{% endif %}>Save</button>
    <button type="button" class="secondary" hx-post="/settings/api-key/test" hx-target="#api-key-form" hx-swap="outerHTML">Test</button>
    <button type="button" class="contrast outline" hx-post="/settings/api-key/clear" hx-target="#api-key-form" hx-swap="outerHTML" hx-confirm="Remove the stored key?" {% if env_locked %}disabled{% endif %}>Clear</button>
  </div>
  {% if message %}<p class="ok">{{ message }}</p>{% endif %}
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  {% if balance %}<p>Balance: <strong>${{ '%.2f'|format(balance.amount) }}</strong> {{ balance.currency }}</p>
    {% with has_api_key=True, oob=True %}{% include "partials/_balance_chip.html" %}{% endwith %}{% endif %}
</form>
```
Note: the last include re-renders the header chip out-of-band (`oob=True` adds `hx-swap-oob`).

```html
{# runwarestudio/web/templates/settings/_general_form.html #}
<form id="general-form" hx-post="/settings" hx-swap="outerHTML" hx-target="this">
  {% for key, sp in spec.items() %}
    <label>{{ key }}
      {% if sp.choices %}
        <select name="{{ key }}">{% for c in sp.choices %}<option value="{{ c }}" {% if values[key] == c %}selected{% endif %}>{{ c }}</option>{% endfor %}</select>
      {% else %}
        <input name="{{ key }}" value="{{ values[key] }}" {% if sp.type.__name__ == 'int' %}type="number"{% endif %}>
      {% endif %}
    </label>
  {% endfor %}
  <button type="submit">Save</button>
  {% if saved %}<p class="ok">Saved.</p>{% endif %}
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
</form>
```

```css
/* runwarestudio/web/static/css/app.css */
.chip{padding:.15rem .6rem;border-radius:1rem;background:var(--pico-secondary-background);font-size:.85rem}
.chip.warn{background:#a35}
.ok{color:var(--pico-ins-color)} .error{color:var(--pico-del-color)}
article.warn{border-left:4px solid #a35}
#toasts{position:fixed;right:1rem;bottom:1rem;display:flex;flex-direction:column;gap:.5rem;z-index:50}
.toast{padding:.6rem 1rem;border-radius:.5rem;background:var(--pico-card-background-color);box-shadow:0 2px 8px rgba(0,0,0,.4)}
.htmx-indicator{opacity:0}.htmx-request .htmx-indicator,.htmx-request.htmx-indicator{opacity:1}
```

```js
// runwarestudio/web/static/js/app.js
function rsToggleTheme() {
  const el = document.documentElement;
  const next = el.dataset.theme === 'dark' ? 'light' : 'dark';
  el.dataset.theme = next;
  const fd = new FormData(); fd.append('ui.theme', next);
  fetch('/settings', { method: 'POST', body: fd });
}
document.addEventListener('htmx:responseError', (e) => {
  const box = document.getElementById('toasts');
  if (!box) return;
  const t = document.createElement('div'); t.className = 'toast error';
  let msg = 'Request failed (' + e.detail.xhr.status + ')';
  try { msg = JSON.parse(e.detail.xhr.responseText).error || msg; } catch (_) {}
  t.textContent = msg; box.appendChild(t); setTimeout(() => t.remove(), 12000);
});
```

`runwarestudio/web/static/img/favicon.svg`: a 32x32 SVG with a rounded square and the letters "RS".

- [ ] **Step 7: Run tests** → `uv run pytest -q` → all pass (previous suites plus 9 new).
- [ ] **Step 8: Commit** → `git add runwarestudio/web tests/conftest.py tests/test_web_health.py tests/test_web_settings.py && git commit -m "feat: FastAPI app with health endpoint, base layout and settings page" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 10: Restart helper and CLI (`serve`, `migrate`, `version`, `doctor`)

**Files:**
- Create: `runwarestudio/services/restart.py`, `runwarestudio/main.py`, `tests/test_restart.py`, `tests/test_cli.py`
- Modify: `runwarestudio/web/routes/system.py` (add `POST /api/restart`, `POST /api/shutdown`)

**Interfaces:**
- Produces:
  - `restart.request_restart(delay: float = 1.5) -> str` returns the strategy chosen: `"launcher"`, `"windows-helper"`, `"execv"`; `restart.request_shutdown(delay=1.0)`. Strategy: env `RUNWARESTUDIO_LAUNCHER=="1"` → `os._exit(75)`; else Windows → spawn `scripts/restart_helper.bat <pid>` detached and `os._exit(0)`; else `os.execv(sys.executable, [sys.executable, *sys.argv])`. The actual exit runs on a `threading.Timer` so the HTTP response is sent first. Internals `_exit(code)` and `_spawn_helper(pid)` are module functions so tests can monkeypatch them.
  - `main.main(argv=None) -> int`; subcommands: `serve [--host H] [--port P] [--open|--no-browser]`, `migrate`, `version`, `doctor`. `main.pick_port(host, port, tries=10) -> tuple[int, bool]` returns `(port, already_ours)`; `main.is_ours(host, port) -> bool` GETs `/api/health` and checks `app == "RunwareStudio"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_restart.py
import sys
from runwarestudio.services import restart

def test_launcher_strategy(monkeypatch):
    called = {}
    monkeypatch.setenv("RUNWARESTUDIO_LAUNCHER", "1")
    monkeypatch.setattr(restart, "_exit", lambda code: called.setdefault("code", code))
    assert restart.request_restart(delay=0) == "launcher"
    restart._pending_join()
    assert called["code"] == 75

def test_windows_helper_strategy(monkeypatch):
    monkeypatch.delenv("RUNWARESTUDIO_LAUNCHER", raising=False)
    monkeypatch.setattr(restart, "_is_windows", lambda: True)
    spawned, exits = [], []
    monkeypatch.setattr(restart, "_spawn_helper", lambda pid: spawned.append(pid))
    monkeypatch.setattr(restart, "_exit", lambda code: exits.append(code))
    assert restart.request_restart(delay=0) == "windows-helper"
    restart._pending_join()
    assert spawned and exits == [0]

def test_execv_strategy(monkeypatch):
    monkeypatch.delenv("RUNWARESTUDIO_LAUNCHER", raising=False)
    monkeypatch.setattr(restart, "_is_windows", lambda: False)
    argv = []
    monkeypatch.setattr(restart, "_execv", lambda a: argv.extend(a))
    assert restart.request_restart(delay=0) == "execv"
    restart._pending_join()
    assert argv[0] == sys.executable
```

```python
# tests/test_cli.py
import socket
from runwarestudio import __version__, main

def test_version_command(capsys):
    assert main.main(["version"]) == 0
    assert __version__ in capsys.readouterr().out

def test_migrate_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNWARESTUDIO_DATA_DIR", str(tmp_path))
    assert main.main(["migrate"]) == 0
    assert (tmp_path / "studio.db").exists()
    assert "schema" in capsys.readouterr().out

def test_doctor_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNWARESTUDIO_DATA_DIR", str(tmp_path))
    assert main.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "data dir" in out and "api key" in out and "git" in out

def test_pick_port_skips_busy_foreign_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    busy = s.getsockname()[1]
    try:
        port, ours = main.pick_port("127.0.0.1", busy, tries=3)
        assert port != busy and ours is False
    finally:
        s.close()

def test_pick_port_returns_free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
    assert main.pick_port("127.0.0.1", free) == (free, False)
```

- [ ] **Step 2: Run to verify failure** → import errors.

- [ ] **Step 3: Implement restart.py**

```python
# runwarestudio/services/restart.py
"""Process restart/shutdown. The exit happens on a timer so the HTTP reply gets out first."""
from __future__ import annotations
import os, subprocess, sys, threading
from ..config import REPO_ROOT, RESTART_EXIT_CODE

_timers: list[threading.Timer] = []


def _is_windows() -> bool:
    return os.name == "nt"


def _exit(code: int) -> None:
    os._exit(code)


def _execv(argv: list[str]) -> None:
    os.execv(argv[0], argv)


def _spawn_helper(pid: int) -> None:
    helper = REPO_ROOT / "scripts" / "restart_helper.bat"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd.exe", "/c", str(helper), str(pid)], cwd=REPO_ROOT, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)


def _schedule(delay: float, fn) -> None:
    t = threading.Timer(delay, fn)
    t.daemon = True
    _timers.append(t)
    t.start()


def _pending_join() -> None:
    """Test helper: wait for scheduled exits to run."""
    for t in _timers:
        t.join(5)
    _timers.clear()


def request_restart(delay: float = 1.5) -> str:
    if os.environ.get("RUNWARESTUDIO_LAUNCHER") == "1":
        _schedule(delay, lambda: _exit(RESTART_EXIT_CODE))
        return "launcher"
    if _is_windows():
        pid = os.getpid()

        def _go():
            _spawn_helper(pid)
            _exit(0)
        _schedule(delay, _go)
        return "windows-helper"
    argv = [sys.executable, *sys.argv]
    if "--open" in argv:
        argv[argv.index("--open")] = "--no-browser"
    _schedule(delay, lambda: _execv(argv))
    return "execv"


def request_shutdown(delay: float = 1.0) -> None:
    _schedule(delay, lambda: _exit(0))
```

Add to `runwarestudio/web/routes/system.py`:

```python
from fastapi import HTTPException
from ...services import restart as restart_svc

def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "testclient", ""):
        raise HTTPException(403, "local only")


@router.post("/api/restart", status_code=202)
async def api_restart(request: Request):
    _local_only(request)
    return {"restarting": True, "strategy": restart_svc.request_restart()}


@router.post("/api/shutdown", status_code=202)
async def api_shutdown(request: Request):
    _local_only(request)
    restart_svc.request_shutdown()
    return {"stopping": True}
```

- [ ] **Step 4: Implement main.py**

```python
# runwarestudio/main.py
"""CLI entry point: serve | migrate | version | doctor."""
from __future__ import annotations
import argparse, logging, os, shutil, socket, sys, threading, webbrowser
import httpx
from . import __version__, config, secrets
from .services import gitinfo, migrate

log = logging.getLogger("runwarestudio")


def is_ours(host: str, port: int) -> bool:
    try:
        r = httpx.get(f"http://{host}:{port}/api/health", timeout=1.5)
        return r.status_code == 200 and r.json().get("app") == "RunwareStudio"
    except Exception:  # noqa: BLE001
        return False


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, port: int, tries: int = 10) -> tuple[int, bool]:
    """Return (port, already_ours). If the requested port runs RunwareStudio, report it.
    Otherwise walk forward until a free port is found."""
    if _port_free(host, port):
        return port, False
    if is_ours(host, port):
        return port, True
    for p in range(port + 1, port + 1 + tries):
        if _port_free(host, p):
            return p, False
    raise SystemExit(f"No free port in {port}..{port + tries}")


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from .web.app import create_app
    paths = config.resolve_paths()
    host = args.host or config.env_str(os.environ, "RUNWARESTUDIO_HOST", config.DEFAULT_HOST)
    want = args.port or config.env_int(os.environ, "RUNWARESTUDIO_PORT", config.DEFAULT_PORT)
    port, ours = pick_port(host, want)
    url = f"http://{host}:{port}/"
    if ours:
        print(f"RunwareStudio is already running at {url}")
        if args.open:
            webbrowser.open(url)
        return 0
    if port != want:
        print(f"Port {want} is busy; using {port}")
    try:
        app = create_app(paths, port=port)
    except migrate.MigrationFailed as e:
        print(str(e), file=sys.stderr)
        return config.MIGRATION_FAIL_EXIT_CODE
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"RunwareStudio {__version__} on {url}  (data: {paths.data})")
    try:
        uvicorn.run(app, host=host, port=port, log_level=os.environ.get("RUNWARESTUDIO_LOG_LEVEL", "info").lower())
    except migrate.MigrationFailed as e:  # raised from lifespan
        print(str(e), file=sys.stderr)
        return config.MIGRATION_FAIL_EXIT_CODE
    return 0


def cmd_migrate(_args: argparse.Namespace) -> int:
    paths = config.resolve_paths()
    config.ensure_dirs(paths)
    try:
        from . import boot
        info = boot.boot(paths)
    except migrate.MigrationFailed as e:
        print(str(e), file=sys.stderr)
        return config.MIGRATION_FAIL_EXIT_CODE
    print(f"schema {info.schema_revision} at {paths.db}")
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    c = gitinfo.current_commit()
    print(f"RunwareStudio {__version__}" + (f" ({c.short} {c.subject})" if c else ""))
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    paths = config.resolve_paths()
    print(f"version    : {__version__}")
    print(f"python     : {sys.version.split()[0]} ({sys.executable})")
    print(f"data dir   : {paths.data} ({'exists' if paths.data.exists() else 'missing'})")
    print(f"database   : {paths.db} schema={migrate.current(paths.db)} head={migrate.head()}")
    print(f"api key    : {secrets.key_source(paths)}")
    print(f"git        : {'checkout' if gitinfo.is_git_install() else 'not a git checkout'} "
          f"{(gitinfo.current_commit() or gitinfo.CommitInfo('', '-', '')).short}")
    print(f"uv         : {os.environ.get('RUNWARESTUDIO_UV') or shutil.which('uv') or 'not found'}")
    print(f"git binary : {os.environ.get('RUNWARESTUDIO_GIT') or shutil.which('git') or 'not found'}")
    print(f"launcher   : {'yes' if os.environ.get('RUNWARESTUDIO_LAUNCHER') == '1' else 'no'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="runwarestudio")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--open", dest="open", action="store_true", help="open the browser")
    g.add_argument("--no-browser", dest="open", action="store_false")
    s.set_defaults(open=False, func=cmd_serve)
    sub.add_parser("migrate", help="create/upgrade the database").set_defaults(func=cmd_migrate)
    sub.add_parser("version", help="print version").set_defaults(func=cmd_version)
    sub.add_parser("doctor", help="print environment diagnostics").set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("RUNWARESTUDIO_LOG_LEVEL", "INFO"))
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests** → `uv run pytest -q` → all pass. Then smoke: `uv run runwarestudio serve --port 8080` in a second terminal, `curl -s localhost:8080/api/health`, Ctrl-C.
- [ ] **Step 6: Commit** → `git add runwarestudio/services/restart.py runwarestudio/main.py runwarestudio/web/routes/system.py tests/test_restart.py tests/test_cli.py && git commit -m "feat: CLI (serve/migrate/version/doctor) and restart service" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 11: Launcher scripts and run.py

**Files:**
- Create: `start.sh`, `start.bat`, `scripts/restart_helper.bat`, `run.py`, `tests/test_scripts.py`

**Interfaces:**
- Consumes: CLI `runwarestudio serve --port P --open|--no-browser`, exit code 75.
- Produces: launchers that loop on 75; `RUNWARESTUDIO_LAUNCHER=1`, `RUNWARESTUDIO_UV`, `RUNWARESTUDIO_GIT`, `RUNWARESTUDIO_HOME` exported.

- [ ] **Step 1: Write the failing test** (static checks; shell behaviour is verified manually in Step 4)

```python
# tests/test_scripts.py
import os, stat, sys
from runwarestudio.config import REPO_ROOT

def test_scripts_exist_and_have_no_powershell():
    for name in ("start.sh", "start.bat", "scripts/restart_helper.bat", "run.py"):
        p = REPO_ROOT / name
        assert p.exists(), name
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        assert "powershell" not in text and ".ps1" not in text, name

def test_start_sh_loops_on_75_and_is_executable():
    p = REPO_ROOT / "start.sh"
    text = p.read_text()
    assert "RUNWARESTUDIO_LAUNCHER=1" in text and "75" in text and 'main "$@"' in text
    if sys.platform != "win32":
        assert stat.S_IMODE(p.stat().st_mode) & stat.S_IXUSR

def test_start_bat_runs_from_temp_copy():
    text = (REPO_ROOT / "start.bat").read_text()
    assert "%TEMP%" in text and "errorlevel 75" in text.replace("ERRORLEVEL", "errorlevel") and "RUNWARESTUDIO_LAUNCHER=1" in text
```

- [ ] **Step 2: Run to verify failure** → assertions fail (files missing).

- [ ] **Step 3: Write the scripts**

```python
# run.py
"""Entry used by launchers: `uv run python run.py serve ...`."""
import sys
from runwarestudio.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

```bash
#!/usr/bin/env bash
# start.sh — Linux/macOS launcher. Loops while the app exits with 75 (restart requested).
main() {
  set -u
  local here
  here="$(cd "$(dirname "$0")" && pwd)"
  cd "$here" || exit 1
  local UV GIT
  UV="${RUNWARESTUDIO_UV:-}"
  if [ -z "$UV" ]; then
    for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
      [ -n "$c" ] && [ -x "$c" ] && UV="$c" && break
    done
  fi
  if [ -z "$UV" ]; then echo "uv not found. Run install.sh first."; exit 1; fi
  GIT="${RUNWARESTUDIO_GIT:-$(command -v git 2>/dev/null || echo git)}"
  export RUNWARESTUDIO_UV="$UV" RUNWARESTUDIO_GIT="$GIT" RUNWARESTUDIO_HOME="$here" RUNWARESTUDIO_LAUNCHER=1
  local PORT="${RUNWARESTUDIO_PORT:-8080}" OPEN="--open" code
  case "${1:-}" in
    --no-browser) OPEN="--no-browser" ;;
    ''|--open) ;;
    *) PORT="$1"; [ "${2:-}" = "--no-browser" ] && OPEN="--no-browser" ;;
  esac
  while true; do
    "$UV" run --frozen python run.py serve --port "$PORT" $OPEN
    code=$?
    if [ "$code" -eq 75 ]; then echo "RunwareStudio: restarting..."; OPEN="--no-browser"; sleep 1; continue; fi
    exit "$code"
  done
}
main "$@"
```

```bat
@echo off
rem start.bat — Windows launcher. Copies itself to %TEMP% and runs the copy so that
rem a git pull can never rewrite the batch file that cmd is currently executing.
setlocal EnableExtensions
if /i "%~1"=="--child" goto :child
set "SELF=%~f0"
set "COPY=%TEMP%\rs_start_%RANDOM%%RANDOM%.bat"
copy /y "%SELF%" "%COPY%" >nul
call "%COPY%" --child "%~dp0" %*
set "RC=%errorlevel%"
del /q "%COPY%" >nul 2>&1
exit /b %RC%

:child
shift
set "HOME_DIR=%~1"
shift
cd /d "%HOME_DIR%"
set "UV=%RUNWARESTUDIO_UV%"
if "%UV%"=="" ( where uv >nul 2>&1 && set "UV=uv" )
if "%UV%"=="" if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if "%UV%"=="" ( echo uv not found. Run install.bat first. & pause & exit /b 1 )
set "GIT=%RUNWARESTUDIO_GIT%"
if "%GIT%"=="" ( where git >nul 2>&1 && set "GIT=git" )
if "%GIT%"=="" if exist "%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe" set "GIT=%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe"
if "%GIT%"=="" if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT=%ProgramFiles%\Git\cmd\git.exe"
set "RUNWARESTUDIO_UV=%UV%"
set "RUNWARESTUDIO_GIT=%GIT%"
set "RUNWARESTUDIO_HOME=%HOME_DIR%"
set "RUNWARESTUDIO_LAUNCHER=1"
set "PORT=%RUNWARESTUDIO_PORT%"
if "%PORT%"=="" set "PORT=8080"
set "OPENFLAG=--open"
if /i "%~1"=="--no-browser" set "OPENFLAG=--no-browser"
if not "%~1"=="" if /i not "%~1"=="--no-browser" if /i not "%~1"=="--open" set "PORT=%~1"
if /i "%~2"=="--no-browser" set "OPENFLAG=--no-browser"
title RunwareStudio
:loop
"%UV%" run --frozen python run.py serve --port %PORT% %OPENFLAG%
if errorlevel 76 goto :done
if errorlevel 75 (
  echo RunwareStudio: restarting...
  set "OPENFLAG=--no-browser"
  timeout /t 1 /nobreak >nul
  goto :loop
)
:done
if not "%errorlevel%"=="0" ( echo RunwareStudio exited with code %errorlevel%. & pause )
exit /b %errorlevel%
```

```bat
@echo off
rem scripts/restart_helper.bat <pid> — used only when the server was NOT started by start.bat.
rem Waits for the old process to exit, then launches start.bat minimised without a browser.
set "PID=%~1"
set /a TRIES=0
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto :go
set /a TRIES+=1
if %TRIES% geq 60 goto :go
timeout /t 1 /nobreak >nul
goto :wait
:go
start "RunwareStudio" /min cmd /c ""%~dp0..\start.bat" --no-browser"
exit
```

Then `chmod +x start.sh`.

- [ ] **Step 4: Verify manually on Linux**

```bash
./start.sh --no-browser &   # note the PID; open http://127.0.0.1:8080/api/health
curl -s -X POST http://127.0.0.1:8080/api/restart   # expect {"restarting":true,"strategy":"launcher"}
sleep 4; curl -s http://127.0.0.1:8080/api/health | grep -o '"boot_id":"[^"]*"'   # different boot_id than before
curl -s -X POST http://127.0.0.1:8080/api/shutdown; wait
```
Expected: the launcher prints "restarting...", the app comes back with a new boot_id, and shutdown ends the loop with exit 0. Record the result in the commit message body.

- [ ] **Step 5: Run tests** → `uv run pytest -q` → all pass.
- [ ] **Step 6: Commit** → `git add run.py start.sh start.bat scripts/restart_helper.bat tests/test_scripts.py && git commit -m "feat: launcher scripts with restart loop (exit 75)" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

## Phase 1 exit criteria

- `uv run pytest -q` green; `uv run ruff check .` clean.
- `./start.sh` opens the browser at `http://127.0.0.1:8080/`, the header shows "No API key", Settings saves a key (masked), Test shows the real balance, `/api/health` reports version, commit and schema.
- `curl -X POST /api/restart` restarts through the launcher loop; migrations run at boot; a second boot with a newer schema head creates a `pre-migrate` backup.
- Phase 2 (Catalog & pricing) starts from the `CatalogModel` table and `client.content.*` facts in the spec.
