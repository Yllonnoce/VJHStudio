"""The constraint harvest: docs pages + the two free API probes, the Models page button
and its status partial, and the `probe` CLI command.

Nothing here touches the network: docs HTML comes from tests/fixtures/docs via an
httpx.MockTransport keyed on the slug, and every RunWare call goes to FakeRunware.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from runware import RunwareError
from sqlalchemy import select

from vjhstudio import db, main
from vjhstudio.models import CatalogModel
from vjhstudio.services import catalog as catalog_svc
from vjhstudio.services import constraints

FIX = Path(__file__).parent / "fixtures" / "docs"

KLING = "klingai:9@4"
IMAGE = "acme:9@1"
ALEPH = "runway:9@2"
I2V = "vjh:i2v-probe@1"
KEY = "abcdefgh1234"

PARAMS_MSG = (
    "Unsupported use of 'vjhProbe' parameter. Allowed values are: 'taskUUID', 'model', "
    "'positivePrompt', 'width', 'height', 'duration'"
)
KLING_DIMS_MSG = (
    "Unsupported use of width/height parameters. "
    "Supported values are: '3840x2160', '2160x3840', '2880x2880'"
)
RULE_DIMS_MSG = (
    "Invalid value for 'width' parameter. Video width must be an integer value "
    "between 128 and 2048, in multiples of 64."
)


def _err(message: str, code: str = "unsupportedParameter") -> RunwareError:
    """A rejection whose derived .code is "validation" (see tests/test_probe.py)."""
    return RunwareError(code, message)


def docs_transport(pages: dict[str, str]):
    """slug -> fixture filename. Any other slug answers 404 (the "no docs page" case)."""

    def handler(request: httpx.Request) -> httpx.Response:
        slug = request.url.path.rsplit("/", 1)[-1]
        name = pages.get(slug)
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, text=(FIX / name).read_text(encoding="utf-8"))

    return httpx.MockTransport(handler)


def _seed(app, rows: list[dict]) -> None:
    with db.session_scope(app.state.boot.session_factory) as s:
        for row in rows:
            s.add(CatalogModel(**row))


def _two_models(app) -> None:
    _seed(
        app,
        [
            {
                "air": KLING,
                "slug": "kling-4k",
                "name": "Kling Probe 4K",
                "kind": "video",
                "capabilities_json": ["io:text-to-video"],
                "source": "curated",
            },
            {
                "air": IMAGE,
                "slug": "not-a-real-docs-page",
                "name": "Acme Probe Image",
                "kind": "image",
                "capabilities_json": ["io:text-to-image"],
                "source": "curated",
            },
        ],
    )


def _stored(app, air: str) -> dict:
    with db.session_scope(app.state.boot.session_factory) as s:
        return dict(catalog_svc.get_by_air(s, air).constraints_json or {})


def _row(state, air: str) -> dict:
    return next(r for r in state.rows if r["air"] == air)


async def _harvest(app, **kw):
    """One harvest over the two seeded models only, with its own state object."""
    kw.setdefault("client_factory", app.state.client_factory)
    kw.setdefault("state", constraints.HarvestState())
    return await constraints.harvest(
        app.state.boot.session_factory,
        airs=[KLING, IMAGE],
        docs_transport=docs_transport({"kling-4k": "kling-4k.html"}),
        **kw,
    )


# --- (a) the happy path ----------------------------------------------------


async def test_harvest_stores_docs_and_api_constraints(client, app, fake):
    _two_models(app)
    fake.script["account_management"] = [[{"balance": 10.0}]] * 12
    fake.script["run"] = [
        _err(PARAMS_MSG),
        _err(KLING_DIMS_MSG),
        _err(PARAMS_MSG),
        _err(RULE_DIMS_MSG),
    ]
    state = await _harvest(app, api_key=KEY)

    kling = _stored(app, KLING)
    assert kling["dims"]["mode"] == "list"
    assert [3840, 2160] in kling["dims"]["list"]
    assert kling["sources"]["api"] and kling["sources"]["docs"]
    assert kling["duration"]["max"] == 15  # from the docs page, not the probes
    assert "width" in kling["params"] and "height" in kling["params"]

    image = _stored(app, IMAGE)
    assert image["dims"] == {"mode": "rule", "min": 128, "max": 2048, "step": 64}
    assert image["sources"]["docs"] is None and image["sources"]["api"]

    assert state.total == 2 and state.done == 2 and state.ok == 2
    assert _row(state, KLING) == {
        "air": KLING,
        "name": "Kling Probe 4K",
        "docs": "ok",
        "api": "ok",
        "dims_mode": "list",
    }
    assert _row(state, IMAGE)["docs"] == "missing"
    assert not state.running and state.finished_at is not None
    assert state.message == "Harvested 2 of 2 models (2 with sizes known)."


# --- (b) no API key --------------------------------------------------------


async def test_harvest_without_a_key_runs_docs_only(client, app, fake):
    _two_models(app)
    state = await _harvest(app, api_key="")

    assert fake.calls == []  # the client was never even opened
    assert state.message == (
        "Add your RunWare API key in Settings to run the API probes; docs pages only."
    )
    kling = _stored(app, KLING)
    assert kling["sources"]["docs"] and kling["sources"]["api"] is None
    assert kling["dims"]["mode"] == "list"  # the docs page has the dimension table
    assert _row(state, KLING)["api"] == "skipped"
    assert _row(state, IMAGE)["docs"] == "missing"


async def test_harvest_with_no_api_flag_sends_nothing(client, app, fake):
    _two_models(app)
    state = await _harvest(app, api_key=KEY, api=False)
    assert fake.calls == []
    # only Kling has a docs page; the other model learned nothing, so it is not "ok"
    assert state.done == 2 and state.ok == 1
    assert state.message == "Harvested 1 of 2 models (1 with sizes known)."


# --- (c) a probe that was accepted -----------------------------------------


async def test_a_billed_probe_stops_the_whole_harvest(client, app, fake):
    _two_models(app)
    fake.script["account_management"] = [[{"balance": 10.0}]] * 12
    # Kling answers both probes; the image model's first probe is *accepted* — which
    # means RunWare would bill for it, so nothing else may be sent.
    fake.script["run"] = [
        _err(PARAMS_MSG),
        _err(KLING_DIMS_MSG),
        [{"taskUUID": "accepted", "imageURL": "http://x/i.png"}],
    ]
    state = await _harvest(app, api_key=KEY)

    assert len(state.rows) == 1 and state.rows[0]["air"] == KLING
    assert state.message.startswith("STOPPED")
    assert "was accepted" in state.message
    # the stopped model is remembered as billed so no harvest ever probes it again
    assert _stored(app, IMAGE)["probe"]["blocked"] is True
    # two probes for Kling plus the one that was accepted, and nothing after it
    assert len([c for c in fake.calls if c[0] == "run"]) == 3


# --- (d) the balance moved -------------------------------------------------


async def test_a_balance_change_stops_the_harvest(client, app, fake):
    _two_models(app)
    fake.script["account_management"] = [
        [{"balance": 10.0}],
        [{"balance": 10.0}],
        [{"balance": 9.0}],
    ] + [[{"balance": 9.0}]] * 8
    fake.script["run"] = [
        _err(PARAMS_MSG),
        _err(KLING_DIMS_MSG),
        _err(PARAMS_MSG),
        _err(RULE_DIMS_MSG),
    ]
    state = await _harvest(app, api_key=KEY)

    assert state.message.startswith("STOPPED: the balance changed")
    assert "before $10.00, after $9.00" in state.message
    # a background generation finishing mid-harvest moves the balance too, and the
    # message has to say so before it asks for a report
    assert "If a generation finished while it ran, that explains it" in state.message
    assert not state.running


async def test_an_unreadable_balance_stops_before_any_probe(client, app, fake):
    _two_models(app)
    fake.script["account_management"] = [RuntimeError("no account")]
    state = await _harvest(app, api_key=KEY)

    assert state.message.startswith("STOPPED")
    assert [c for c in fake.calls if c[0] == "run"] == []
    assert _row(state, KLING)["docs"] == "ok"  # the free docs data is still kept
    assert _row(state, KLING)["api"] == "skipped"


async def test_a_per_model_error_does_not_stop_the_others(client, app, fake):
    _two_models(app)
    fake.script["account_management"] = [[{"balance": 10.0}]] * 12
    fake.script["run"] = [
        _err("The model is offline", "connectionFailed"),
        _err(PARAMS_MSG),
        _err(RULE_DIMS_MSG),
    ]
    state = await _harvest(app, api_key=KEY)

    assert _row(state, KLING)["api"].startswith("error:")
    assert _row(state, IMAGE)["api"] == "ok"
    # Kling still learned its sizes from the docs page, so it counts as harvested
    assert state.done == 2 and state.ok == 2
    assert state.message == "Harvested 2 of 2 models (2 with sizes known). 1 had errors."


async def test_a_crash_in_one_row_cancels_the_queued_probes(client, app, fake):
    """Nothing may be sent after the closing balance read — including by a probe that
    was still queued when the run fell over."""
    _two_models(app)
    fake.script["account_management"] = [[{"balance": 10.0}]] * 12
    fake.script["run"] = [
        _err(PARAMS_MSG),
        _err(KLING_DIMS_MSG),
        _err(PARAMS_MSG),
        _err(RULE_DIMS_MSG),
    ]
    state = constraints.HarvestState()
    state.add_row = lambda row: (_ for _ in ()).throw(RuntimeError("row exploded"))

    out = await _harvest(app, api_key=KEY, state=state)

    assert out.message == "Harvest failed: row exploded"
    # Kling's two probes and nothing else: the image model's probe never started
    assert len([c for c in fake.calls if c[0] == "run"]) == 2
    # and the balance was still read on the way out (run before/after + Kling's pair)
    assert len([c for c in fake.calls if c[0] == "account_management"]) == 4
    assert not out.running


async def test_a_client_that_will_not_open_keeps_the_docs_results(client, app, fake):
    @asynccontextmanager
    async def broken_factory(api_key: str, transport: str = "rest"):
        raise RuntimeError("no connection")
        yield  # pragma: no cover - never reached

    _two_models(app)
    state = await _harvest(app, api_key=KEY, client_factory=broken_factory)

    assert state.message.startswith("STOPPED: could not reach RunWare")
    assert fake.calls == []
    assert _row(state, KLING) == {
        "air": KLING,
        "name": "Kling Probe 4K",
        "docs": "ok",
        "api": "skipped",
        "dims_mode": "list",
    }
    assert _stored(app, KLING)["sources"]["docs"]  # the free half was kept


async def test_harvest_refuses_to_join_a_running_state(client, app):
    state = constraints.HarvestState()
    assert state.begin() is True
    with pytest.raises(RuntimeError, match="already running"):
        await _harvest(app, api_key="", state=state)
    assert state.running and state.rows == []  # untouched


# --- (e) the Models page ---------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """The routes read the module-level STATE; every test gets its own."""
    monkeypatch.setattr(constraints, "STATE", constraints.HarvestState())


async def test_models_page_offers_the_harvest_button(client):
    r = await client.get("/models")
    assert r.status_code == 200
    assert "Harvest constraints" in r.text
    assert "Nothing is generated." in r.text
    assert 'hx-trigger="every 2s"' not in r.text


async def test_harvest_post_polls_then_reports(client, app, monkeypatch):
    release = asyncio.Event()

    async def fake_harvest(session_factory, *, state=None, **kw):
        st = state or constraints.STATE
        st.total, st.done = 2, 1
        await release.wait()
        st.add_row({"air": KLING, "name": "Kling", "docs": "ok", "api": "ok", "dims_mode": "list"})
        st.message = "Harvested 2 of 2 models."
        st.finish()
        return st

    monkeypatch.setattr(constraints, "harvest", fake_harvest)

    r = await client.post("/models/harvest")
    assert r.status_code == 200
    assert "Harvesting…" in r.text
    assert 'hx-trigger="every 2s"' in r.text

    busy = await client.post("/models/harvest")
    assert busy.status_code == 409 and "already running" in busy.text

    poll = await client.get("/hx/models/harvest-status")
    assert poll.status_code == 200 and "Harvesting…" in poll.text

    release.set()
    await app.state.harvest_task

    done = await client.get("/hx/models/harvest-status")
    assert "Harvested 2 of 2 models." in done.text
    assert 'hx-trigger="every 2s"' not in done.text
    assert "Harvest constraints" in done.text  # the button comes back
    assert KLING in done.text  # the per-model table


async def test_harvest_is_local_only(app, monkeypatch):
    monkeypatch.setattr(constraints, "harvest", lambda *a, **k: asyncio.sleep(0))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("10.0.0.9", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as outside:
            r = await outside.post("/models/harvest")
            # the poll prints the account balance when a run was stopped
            poll = await outside.get("/hx/models/harvest-status")
    assert r.status_code == 403 and poll.status_code == 403
    assert not constraints.STATE.running


async def test_row_badges_show_known_sizes_and_unsupported_models(client, app):
    _seed(
        app,
        [
            {
                "air": KLING,
                "name": "Kling Probe 4K",
                "kind": "video",
                "capabilities_json": ["io:text-to-video"],
                "source": "curated",
                "constraints_json": {"dims": {"mode": "list", "list": [[3840, 2160]]}},
            },
            {
                "air": ALEPH,
                "name": "Aleph Probe",
                "kind": "video",
                "capabilities_json": ["io:video-to-video"],
                "source": "curated",
                "constraints_json": {
                    "dims": {"mode": "unknown"},
                    "inputs": {"video": {"required": True}},
                },
            },
            {
                "air": I2V,
                "name": "FrameStart Probe",
                "kind": "video",
                "capabilities_json": ["io:image-to-video"],
                "source": "curated",
            },
        ],
    )
    body = (await client.get("/models")).text

    def row_html(name: str) -> str:
        return next(chunk for chunk in body.split("<tr ") if name in chunk)

    assert "sizes known" in row_html("Kling Probe 4K")
    assert "video-to-video only" not in row_html("Kling Probe 4K")
    aleph = row_html("Aleph Probe")
    assert "video-to-video only — not supported yet" in aleph
    assert "sizes known" not in aleph
    # the row badge is services/catalog.badge(), so the first-frame case shows here too
    # (as its own chip, not only inside the label's title attribute)
    assert '<small class="chip warn">needs a first frame</small>' in row_html("FrameStart Probe")


# --- (f) the CLI -----------------------------------------------------------


def test_probe_command_help_exits_zero():
    with pytest.raises(SystemExit) as e:
        main.build_parser().parse_args(["probe", "--help"])
    assert e.value.code == 0


def test_probe_command_flags():
    args = main.build_parser().parse_args(
        ["probe", "--kind", "video", "--air", "a:1@1", "--air", "b:2@2", "--no-docs"]
    )
    assert args.kind == ["video"] and args.air == ["a:1@1", "b:2@2"]
    assert args.docs is False and args.api is True
    assert main.build_parser().parse_args(["probe"]).kind == []


# --- per-model balance guard and blocked rows ------------------------------


async def test_a_balance_change_after_one_model_stops_before_the_next(client, app, fake):
    _two_models(app)
    # run-before 10, Kling-before 10, Kling-after 9 -> stop right there
    fake.script["account_management"] = [
        [{"balance": 10.0}],
        [{"balance": 10.0}],
        [{"balance": 9.0}],
    ] + [[{"balance": 9.0}]] * 8
    fake.script["run"] = [_err(PARAMS_MSG), _err(KLING_DIMS_MSG), _err(PARAMS_MSG)]
    state = await _harvest(app, api_key=KEY)

    assert state.message.startswith("STOPPED: the balance changed")
    assert len([c for c in fake.calls if c[0] == "run"]) == 2  # Kling only; IMAGE never probed
    assert _stored(app, KLING)["probe"]["blocked"] is True
    assert _stored(app, IMAGE) == {}


async def test_a_blocked_model_is_never_probed_again(client, app, fake):
    _two_models(app)
    with db.session_scope(app.state.boot.session_factory) as s:
        m = s.execute(select(CatalogModel).where(CatalogModel.air == KLING)).scalar_one()
        m.constraints_json = {"probe": {"blocked": True, "reason": "billed once", "at": "t"}}
    fake.script["account_management"] = [[{"balance": 10.0}]] * 12
    fake.script["run"] = [_err(PARAMS_MSG), _err(KLING_DIMS_MSG)]  # the image model's probes
    state = await _harvest(app, api_key=KEY)

    assert _row(state, KLING)["api"] == constraints.SKIPPED_BLOCKED
    sent = [p for n, p in fake.calls if n == "run"]
    assert len(sent) == 2 and {p["model"] for p in sent} == {IMAGE}


async def test_unsafe_providers_get_docs_only(client, app, fake):
    _seed(app, [{"air": "luma:ray@3.2", "slug": "kling-4k", "name": "Ray 3.2", "kind": "video"}])
    fake.script["account_management"] = [[{"balance": 10.0}]] * 6
    fake.script["run"] = []
    state = await constraints.harvest(
        app.state.boot.session_factory,
        client_factory=app.state.client_factory,
        state=constraints.HarvestState(),
        api_key=KEY,
        airs=["luma:ray@3.2"],
        docs_transport=docs_transport({"kling-4k": "kling-4k.html"}),
    )

    assert _row(state, "luma:ray@3.2")["api"].startswith("skipped: this provider accepts unknown")
    assert [c for c in fake.calls if c[0] == "run"] == []
    assert _stored(app, "luma:ray@3.2")["dims"]["mode"] == "list"  # the docs page still counted
