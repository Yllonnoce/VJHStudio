"""The MCP tools, driven by the SDK's in-memory client.

Every test goes through a real ``Client`` so the argument schemas, the structured
returns and the refusals are exercised exactly as an agent host would see them.
A refusal arrives as ``is_error`` with the sentence inside
"Error executing tool <name>: ...", so the assertions look for a substring.
"""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from mcp.client import Client

from vjhstudio import db
from vjhstudio.mcp.server import MCPContext, build_server
from vjhstudio.models import CatalogModel, Job
from vjhstudio.services import catalog, settings

FLUX = "runware:101@1"
LTX = "lightricks:ltx@2.3"
VEO = "google:3@2"  # generateAudio defaults on, and doubles the per-second rate
WAN = "alibaba:wan@2.7"  # a list-mode model with a 720p and a 1080p rate
KLING4K = "klingai:kling-video@3-4k"  # a list-mode model whose only sizes are 4K
NANO = "google:4@2"  # lists sizes ImageRequest will not take (1376x768, 6336x2688 ...)
FIRST_FRAME = "tests:firstframe@1"

IMAGE_REPLY = [{"imageURL": "http://x/1.png", "seed": 5, "cost": 0.004}]


def _context(app, runner=None):
    return MCPContext(
        session_factory=app.state.boot.session_factory,
        paths=app.state.paths,
        runner=runner or (lambda: app.state.runner),
        env=app.state.env,
        setting=app.state.setting,
        base_url="http://test",
    )


@asynccontextmanager
async def open_mcp(ctx):
    """Open and close the client inside one task of its own.

    pytest-asyncio runs a fixture's setup and its teardown in two different tasks, and
    the SDK's client holds an anyio task group, which refuses to be left from a task
    other than the one that entered it."""
    ready: asyncio.Future = asyncio.get_running_loop().create_future()
    done = asyncio.Event()

    async def hold():
        try:
            async with Client(build_server(ctx)) as c:
                ready.set_result(c)
                await done.wait()
        except BaseException as e:  # noqa: BLE001 - the waiter must never hang
            if not ready.done():
                ready.set_exception(e)
            raise

    task = asyncio.create_task(hold())
    try:
        yield await ready
    finally:
        done.set()
        await task


class _Recorder:
    """A runner stand-in: the job is queued and priced, and never actually run."""

    def __init__(self):
        self.submitted: list[str] = []

    def submit(self, job_id: str) -> None:
        self.submitted.append(job_id)

    def cancel(self, job_id: str) -> bool:
        return False

    def snapshot(self) -> dict:
        return {}


@pytest.fixture
async def ctx(app, client):
    """``client`` keeps the app lifespan -- and therefore ``boot`` and the runner -- alive."""
    return _context(app)


@pytest.fixture
async def mcp(ctx):
    async with open_mcp(ctx) as c:
        yield c


@pytest.fixture
async def quoted(app, client):
    """A server whose runner only records submits: these tests are about what a job is
    priced and queued as, not about running it."""
    recorder = _Recorder()
    async with open_mcp(_context(app, runner=lambda: recorder)) as c:
        yield c


def _data(result):
    """A dict return arrives as JSON text; a list return is wrapped as {"result": [...]}"""
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


def _seed_first_frame_model(app):
    """No curated video row demands a first frame, so the refusal needs one of its own."""
    with db.session_scope(app.state.boot.session_factory) as s:
        s.add(
            CatalogModel(
                air=FIRST_FRAME,
                name="First Frame Only",
                kind="video",
                capabilities_json=["io:image-to-video"],
                price_unit="per_second",
                price_primary=0.05,
                constraints_json={"inputs": {"frameImages": {"required": True, "max_items": 2}}},
                source="curated",
            )
        )


async def test_tools_are_listed(mcp):
    names = {t.name for t in (await mcp.list_tools()).tools}
    assert {
        "list_models", "model_details", "estimate", "generate_image", "generate_video",
        "job_status", "wait_for_job", "cancel_job", "list_jobs", "list_outputs",
        "output_details", "move_output", "list_projects", "create_project", "list_assets",
        "account",
    } <= names  # fmt: skip


async def test_wait_for_job_hides_the_injected_context(mcp):
    tool = next(t for t in (await mcp.list_tools()).tools if t.name == "wait_for_job")
    assert set(tool.input_schema["properties"]) == {"job_id", "timeout_s"}


async def test_list_models_shape(mcp):
    reply = _data(await mcp.call_tool("list_models", {"kind": "image"}))
    rows = reply["models"]
    assert rows[0]["air"] == FLUX and rows[0]["default"] is True  # the default model leads
    flux = rows[0]
    assert flux["accepts"] == "seed image" and flux["price_usd"] > 0
    assert flux["unit"] == "per_image" and flux["needs_first_frame"] is False
    assert flux["sizes_known"] is True  # rule mode: the model told us its grid
    by_name = _data(await mcp.call_tool("list_models", {"kind": "image", "sort": "name"}))["models"]
    rest = [r["name"].lower() for r in by_name[1:]]  # after the default model
    assert rest == sorted(rest)
    res = await mcp.call_tool("list_models", {"kind": "audio"})
    assert res.is_error and "image" in res.content[0].text


async def test_list_models_is_short_by_default_and_searchable(mcp, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        for i in range(30):  # a catalog bigger than one reply should carry
            catalog.upsert_row(
                s,
                {
                    "air": f"test:model@{i}",
                    "name": f"Test Model {i}",
                    "kind": "image",
                    "capabilities": ["io:text-to-image"],
                    "price": {"unit": "per_image", "primary": 0.001 * (i + 1), "tiers": {}},
                },
                source="curated",
            )
    reply = _data(await mcp.call_tool("list_models", {"kind": "image"}))
    assert reply["shown"] == 20 and reply["total"] > 20 and "showing 20 of" in reply["note"]
    reply = _data(await mcp.call_tool("list_models", {"kind": "image", "limit": 3}))
    assert reply["shown"] == 3 and reply["models"][0]["default"] is True
    reply = _data(await mcp.call_tool("list_models", {"search": "test model 7"}))
    assert [m["air"] for m in reply["models"]] == ["test:model@7"] and reply["note"] == ""


async def test_model_details_and_estimates(mcp):
    d = _data(await mcp.call_tool("model_details", {"air": FLUX}))
    assert d["kind"] == "image" and d["roles"]["seed"]["max"] == 1 and d["sizes"]
    img = _data(
        await mcp.call_tool("estimate", {"kind": "image", "air": FLUX, "number_results": 2})
    )
    assert img["estimate_usd"] == pytest.approx(0.0076, rel=1e-3)
    vid = _data(
        await mcp.call_tool(
            "estimate", {"kind": "video", "air": LTX, "duration": 5, "resolution": "720p"}
        )
    )
    assert vid["estimate_usd"] > 0 and vid["rate"] > 0
    res = await mcp.call_tool("model_details", {"air": "nope:1@1"})
    assert res.is_error and "list_models" in res.content[0].text


async def test_generate_image_runs_to_an_output_with_url_and_path(mcp, fake):
    fake.script["run"] = [IMAGE_REPLY]
    r = _data(
        await mcp.call_tool(
            "generate_image", {"air": FLUX, "prompt": "a red fox", "project": "default"}
        )
    )
    assert r["job_id"] and r["estimate_usd"] > 0 and r["cap"]["jobs_today"] == 1
    done = _data(await mcp.call_tool("wait_for_job", {"job_id": r["job_id"], "timeout_s": 30}))
    assert done["status"] == "succeeded", done
    assert done["source"] == "mcp" and done["cost"] == pytest.approx(0.004)
    out = done["outputs"][0]
    assert out["url"].startswith("http://test/files/outputs/default/") and out["path"].endswith(
        ".png"
    )
    assert out["project"] == "default"

    same = _data(await mcp.call_tool("job_status", {"job_id": r["job_id"]}))
    assert same["status"] == "succeeded"
    listed = _data(await mcp.call_tool("list_jobs", {}))
    assert listed[0]["id"] == r["job_id"]
    gallery = _data(await mcp.call_tool("list_outputs", {"project": "default"}))
    assert gallery[0]["id"] == out["id"]
    detail = _data(await mcp.call_tool("output_details", {"output_id": out["id"]}))
    assert detail["prompt"] == "a red fox" and detail["seed"] == 5


async def test_size_snapping_is_reported(mcp, fake):
    fake.script["run"] = [IMAGE_REPLY]
    r = _data(
        await mcp.call_tool(
            "generate_image", {"air": FLUX, "prompt": "x", "width": 1000, "height": 1000}
        )
    )
    assert "1024" in r["note"]


async def test_generate_video_needing_a_first_frame_is_refused(mcp, app):
    _seed_first_frame_model(app)
    res = await mcp.call_tool(
        "generate_video", {"air": FIRST_FRAME, "prompt": "x", "project": "default"}
    )
    assert res.is_error and "first-frame" in res.content[0].text
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(Job).count() == 0


async def test_cap_refusal_leaves_no_job(mcp, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        settings.set_many(s, {"mcp.daily_cap_usd": "0.001"})
    res = await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "x"})
    assert res.is_error and "cap" in res.content[0].text
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(Job).count() == 0


async def test_project_by_name_slug_or_id_and_move(mcp, fake):
    fake.script["run"] = [IMAGE_REPLY]
    p = _data(await mcp.call_tool("create_project", {"name": "Campaign One"}))
    assert p["slug"] == "campaign-one"
    r = _data(
        await mcp.call_tool(
            "generate_image", {"air": FLUX, "prompt": "x", "project": "Campaign One"}
        )
    )
    done = _data(await mcp.call_tool("wait_for_job", {"job_id": r["job_id"], "timeout_s": 30}))
    assert done["status"] == "succeeded", done
    oid = done["outputs"][0]["id"]
    moved = _data(await mcp.call_tool("move_output", {"output_id": oid, "project": "default"}))
    assert moved["project"] == "default"
    listed = _data(await mcp.call_tool("list_projects", {}))
    assert {row["slug"] for row in listed} == {"default", "campaign-one"}
    assert any(row["id"] == p["id"] for row in listed)
    # an unknown project name files the job under Default and says so
    fake.script["run"] = [IMAGE_REPLY]
    r = _data(
        await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "x", "project": "nope"})
    )
    assert r["project"] == "default" and "does not exist" in r["note"]
    # but moving to a project that does not exist is still refused
    res = await mcp.call_tool("move_output", {"output_id": oid, "project": "nope"})
    assert res.is_error and "list_projects" in res.content[0].text


async def test_cancel_job_stops_a_queued_job(mcp, app, fake):
    """One worker, the first job gated open: the second is provably still queued, so the
    cancel is deterministic and the row must end up ``cancelled``."""
    gate = asyncio.Event()
    scripted = fake.run

    async def slow(params, options=None):
        await gate.wait()
        return await scripted(params, options)

    fake.run = slow
    fake.script["run"] = [IMAGE_REPLY]
    app.state.runner.set_concurrency(1)

    first = _data(await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "one"}))
    second = _data(await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "two"}))
    await asyncio.sleep(0.05)
    assert app.state.runner.active_ids() == [first["job_id"]]

    assert _data(await mcp.call_tool("cancel_job", {"job_id": second["job_id"]}))["cancelled"]
    gate.set()
    await app.state.runner.wait_idle()
    done = _data(await mcp.call_tool("job_status", {"job_id": second["job_id"]}))
    assert done["status"] == "cancelled"
    assert _data(await mcp.call_tool("job_status", {"job_id": first["job_id"]}))["status"] == (
        "succeeded"
    )


async def test_unknown_job_ids_are_refused(mcp):
    for tool in ("job_status", "cancel_job", "wait_for_job"):
        res = await mcp.call_tool(tool, {"job_id": "nope"})
        assert res.is_error and "nope" in res.content[0].text


async def test_generate_refuses_when_the_runner_is_missing(app, client):
    """A refusal must not leave a queued, cap-consuming row behind for the next boot."""
    async with open_mcp(_context(app, runner=lambda: None)) as m:
        res = await m.call_tool("generate_image", {"air": FLUX, "prompt": "x"})
        assert res.is_error and "runner is not running" in res.content[0].text
        res = await m.call_tool("generate_video", {"air": LTX, "prompt": "x"})
        assert res.is_error and "runner is not running" in res.content[0].text
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(Job).count() == 0


async def test_account_assets_and_resources(mcp):
    acct = _data(await mcp.call_tool("account", {}))
    assert "cap" in acct and "spent_today_usd" in acct
    assert acct["cap"]["max_jobs"] == 20
    assert _data(await mcp.call_tool("list_assets", {})) == []
    uris = {str(r.uri) for r in (await mcp.list_resources()).resources}
    assert {"vjhstudio://projects", "vjhstudio://outputs/recent"} <= uris
    body = (await mcp.read_resource("vjhstudio://projects")).contents[0]
    assert json.loads(body.text)[0]["slug"] == "default"
    names = {p.name for p in (await mcp.list_prompts()).prompts}
    assert "plan_shoot" in names


# ---- price exactly what is queued ----------------------------------------
LOOSE = "tests:loose@1"


def _hx(client, query: str):
    return client.get("/hx/generate/estimate?" + query)


async def test_video_estimate_prices_the_audio_the_model_defaults_to(mcp, client):
    """Veo's ``generateAudio`` defaults to true and doubles the per-second rate. The form
    never omits a schema-declared boolean, and neither may the tool."""
    r = _data(
        await mcp.call_tool(
            "estimate", {"kind": "video", "air": VEO, "duration": 8, "resolution": "720p"}
        )
    )
    assert r["audio"] is True and r["estimate_usd"] == pytest.approx(0.4 * 8)
    hx = await _hx(client, f"mode=video&air={VEO}&duration=8&resolution=720p&ps_generateAudio=on")
    assert f"\u2248${r['estimate_usd']:.2f}" in hx.text

    off = _data(
        await mcp.call_tool(
            "estimate",
            {
                "kind": "video",
                "air": VEO,
                "duration": 8,
                "resolution": "720p",
                "audio": False,
            },
        )
    )
    assert off["estimate_usd"] == pytest.approx(0.2 * 8)


async def test_generate_video_prices_the_settings_the_job_will_carry(quoted, app):
    """The blocker: priced from the agent's arguments alone an 8 s Veo clip looks like
    $1.60 and slips under the $2 cap, then bills $3.20."""
    res = await quoted.call_tool(
        "generate_video", {"air": VEO, "prompt": "x", "duration": 8, "resolution": "720p"}
    )
    assert res.is_error and "$3.20" in res.content[0].text
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(Job).count() == 0
        settings.set_many(s, {"mcp.daily_cap_usd": "10"})

    r = _data(
        await quoted.call_tool(
            "generate_video", {"air": VEO, "prompt": "x", "duration": 8, "resolution": "720p"}
        )
    )
    assert r["estimate_usd"] == pytest.approx(3.2)
    assert r["provider_settings"] == {"generateAudio": True}
    with db.session_scope(app.state.boot.session_factory) as s:
        job = s.get(Job, r["job_id"])
        assert job.request_json["provider_settings"] == {"generateAudio": True}
        assert job.request_json["_estimate"] == pytest.approx(3.2)
        assert job.request_json["duration"] == 8


async def test_video_is_priced_at_the_tier_of_the_size_it_will_render(quoted, client, app):
    """Wan lists 1920x1080 and charges the 1080p rate for it; the pixels win over the
    preset name inside ``build_video_task``, so the estimate must follow them."""
    picked = {"air": WAN, "width": 1920, "height": 1080, "duration": 5, "resolution": "720p"}
    r = _data(await quoted.call_tool("estimate", {"kind": "video", **picked}))
    assert r["resolution"] == "1080p" and r["estimate_usd"] == pytest.approx(0.15 * 5)
    hx = await _hx(client, f"mode=video&air={WAN}&duration=5&resolution=1080p")
    assert f"\u2248${r['estimate_usd']:.2f}" in hx.text

    g = _data(await quoted.call_tool("generate_video", {"prompt": "x", **picked}))
    assert g["estimate_usd"] == pytest.approx(0.75) and g["resolution"] == "1080p"
    with db.session_scope(app.state.boot.session_factory) as s:
        job = s.get(Job, g["job_id"])
        assert (job.request_json["width"], job.request_json["height"]) == (1920, 1080)
        assert job.request_json["resolution"] == "1080p"


async def test_a_model_with_no_known_sizes_keeps_the_preset(quoted, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        s.add(
            CatalogModel(
                air=LOOSE,
                name="Loose Video",
                kind="video",
                capabilities_json=["io:text-to-video"],
                price_unit="per_second",
                price_primary=0.02,
                constraints_json={"dims": {"mode": "unknown"}},
                source="curated",
            )
        )
    rows = _data(await quoted.call_tool("list_models", {"kind": "video", "search": LOOSE}))[
        "models"
    ]
    assert next(r for r in rows if r["air"] == LOOSE)["sizes_known"] is False

    r = _data(
        await quoted.call_tool(
            "generate_video", {"air": LOOSE, "prompt": "x", "width": 1000, "height": 1000}
        )
    )
    assert "lists no sizes" in r["note"]
    with db.session_scope(app.state.boot.session_factory) as s:
        job = s.get(Job, r["job_id"])
        assert job.request_json["width"] is None and job.request_json["height"] is None


async def test_a_size_ImageRequest_would_reject_is_refused_with_the_ones_that_work(mcp):
    """google:4@2 lists 1376x768 and 6336x2688; ``ImageRequest`` takes neither. The agent
    gets the sizes that do work, not a pydantic dump naming a number it never sent."""
    picked = {"air": NANO, "width": 1920, "height": 1080}
    res = await mcp.call_tool("estimate", {"kind": "image", **picked})
    text = res.content[0].text
    assert res.is_error and "multiple of 64" in text and "1024x1024" in text
    res = await mcp.call_tool("generate_image", {"prompt": "x", **picked})
    assert res.is_error and "1024x1024" in res.content[0].text


async def test_a_rule_model_is_priced_at_the_tier_its_pixels_will_bill_at(quoted, client, app):
    """LTX is a ``rule`` model: 1920x1080 is snapped to its 1080p pixels (1920x1088) and
    billed at the 1080p rate, whatever the ``resolution`` argument was left at. Priced at
    720p the cap would admit twice the clips it can actually pay for."""
    picked = {"air": LTX, "width": 1920, "height": 1080, "duration": 5}
    r = _data(await quoted.call_tool("estimate", {"kind": "video", **picked}))
    assert r["resolution"] == "1080p" and r["estimate_usd"] == pytest.approx(0.08 * 5)
    hx = await _hx(client, f"mode=video&air={LTX}&duration=5&resolution=1080p")
    assert f"≈${r['estimate_usd']:.2f}" in hx.text

    g = _data(
        await quoted.call_tool("generate_video", {"prompt": "x", "resolution": "720p", **picked})
    )
    assert g["estimate_usd"] == pytest.approx(0.4) and g["resolution"] == "1080p"
    with db.session_scope(app.state.boot.session_factory) as s:
        job = s.get(Job, g["job_id"])
        assert (job.request_json["width"], job.request_json["height"]) == (1920, 1088)
        assert job.request_json["resolution"] == "1080p"
        assert job.request_json["_estimate"] == pytest.approx(0.4)


async def test_a_list_model_with_no_size_still_gets_one_it_offers(quoted, app):
    """The panel never posts an off-list size and neither may the tool: Kling 4K lists
    only 3840x2160, 2160x3840 and 2880x2880, so the default 720p preset's 1280x720 is a
    size it never offered -- and it bills at the 4K rate either way."""
    with db.session_scope(app.state.boot.session_factory) as s:
        settings.set_many(s, {"mcp.daily_cap_usd": "10"})
    r = _data(
        await quoted.call_tool("generate_video", {"air": KLING4K, "prompt": "x", "duration": 5})
    )
    assert r["resolution"] == "4K" and r["estimate_usd"] == pytest.approx(0.42 * 5)
    assert "3840x2160" in r["note"]
    with db.session_scope(app.state.boot.session_factory) as s:
        job = s.get(Job, r["job_id"])
        assert (job.request_json["width"], job.request_json["height"]) == (3840, 2160)
        assert job.request_json["resolution"] == "4K"


# ---- every argument but the prompt has a default; nulls are tolerated ------
async def test_no_tool_but_the_prompt_requires_an_argument(mcp):
    """Local models call tools with whatever they remember; a missing or null
    argument must fall back to something sensible, not a validation error."""
    tools = {t.name: t for t in (await mcp.list_tools()).tools}
    for name, tool in tools.items():
        required = set(tool.input_schema.get("required", []))
        if name in ("generate_image", "generate_video"):
            assert required == {"prompt"}, (name, required)
        elif name == "create_project":
            assert required == {"name"}, (name, required)
        elif name == "move_output":
            assert required == {"project"}, (name, required)
        else:
            assert not required, (name, required)


async def test_list_models_without_a_kind_lists_both(mcp):
    rows = _data(await mcp.call_tool("list_models", {"limit": 200}))["models"]
    kinds = {r["kind"] for r in rows}
    assert kinds == {"image", "video"}
    rows = _data(await mcp.call_tool("list_models", {"kind": None, "sort": None, "limit": 200}))
    assert {r["kind"] for r in rows["models"]} == {"image", "video"}


async def test_estimate_and_details_default_to_the_configured_models(mcp):
    e = _data(await mcp.call_tool("estimate", {}))
    assert e["air"] == FLUX and e["estimate_usd"] > 0
    e = _data(await mcp.call_tool("estimate", {"air": LTX}))  # kind inferred
    assert e["air"] == LTX and e["resolution"]
    e = _data(await mcp.call_tool("estimate", {"kind": "video"}))
    assert e["air"] == LTX
    d = _data(await mcp.call_tool("model_details", {}))
    assert d["air"] == FLUX


async def test_generate_image_defaults_everything_but_the_prompt(mcp, fake):
    fake.script["run"] = [IMAGE_REPLY]
    r = _data(
        await mcp.call_tool(
            "generate_image",
            {"prompt": "a red fox", "project": None, "negative_prompt": None, "width": None},
        )
    )
    assert r["job_id"]
    done = _data(await mcp.call_tool("wait_for_job", {}))  # no id: the latest agent job
    assert done["id"] == r["job_id"] and done["status"] == "succeeded", done
    assert done["model"] == FLUX
    status = _data(await mcp.call_tool("job_status", {"job_id": None}))
    assert status["id"] == r["job_id"]
    latest = _data(await mcp.call_tool("output_details", {}))
    assert latest["id"] == done["outputs"][0]["id"]
    res = await mcp.call_tool("generate_image", {})
    assert res.is_error and "prompt" in res.content[0].text.lower()


async def test_null_filters_are_ignored(mcp):
    rows = _data(await mcp.call_tool("list_jobs", {"status": None, "limit": None}))
    assert isinstance(rows, list)
    rows = _data(
        await mcp.call_tool("list_outputs", {"project": None, "kind": None, "search": None})
    )
    assert isinstance(rows, list)
    rows = _data(await mcp.call_tool("list_assets", {"kind": None, "search": None, "limit": "5"}))
    assert isinstance(rows, list)
    p = _data(await mcp.call_tool("create_project", {"name": "Nulls", "description": None}))
    assert p["slug"] == "nulls"


async def test_job_tools_without_any_job_say_so(mcp):
    res = await mcp.call_tool("job_status", {})
    assert res.is_error and "no agent job" in res.content[0].text.lower()


async def test_the_default_tier_is_the_models_own(mcp):
    """A model that only runs at 4K must not be estimated, or queued, at 720p."""
    d = _data(await mcp.call_tool("model_details", {"air": "klingai:kling-video@3-4k"}))
    assert d["defaults"]["resolution"].startswith("4K") and d["defaults"]["duration"] > 0
    e = _data(await mcp.call_tool("estimate", {"air": "klingai:kling-video@3-4k"}))
    assert e["resolution"].startswith("4K")
    e = _data(await mcp.call_tool("estimate", {"air": LTX}))
    assert e["resolution"] == "720p"


async def test_every_call_is_traced_with_its_arguments(mcp, ctx):
    await mcp.call_tool("list_projects", {})
    res = await mcp.call_tool("list_models", {"kind": "audio"})
    assert res.is_error
    calls = ctx.trace.recent(5)
    assert calls[0]["tool"] == "list_models" and calls[0]["ok"] is False
    assert '"kind": "audio"' in calls[0]["arguments"] and "image" in calls[0]["error"]
    assert calls[1]["tool"] == "list_projects" and calls[1]["ok"] is True


async def test_examples_ride_in_descriptions_and_schemas(mcp):
    tools = {t.name: t for t in (await mcp.list_tools()).tools}
    gen = tools["generate_image"]
    assert "Example arguments:" in gen.description and '"prompt"' in gen.description
    assert gen.input_schema.get("examples") == [
        {"prompt": "a red fox in snow, golden hour", "project": "Default"}
    ]


async def test_a_malformed_call_is_taught_the_right_shape(mcp):
    res = await mcp.call_tool("generate_image", {"prompt": ["a", "fox"], "width": "big"})
    text = res.content[0].text
    assert res.is_error
    assert "prompt: Input should be a valid string" in text
    assert "width: Input should be a valid integer" in text
    assert 'You sent {"prompt": ["a", "fox"], "width": "big"}' in text
    assert 'A correct call looks like {"prompt": "a red fox in snow, golden hour"' in text


async def test_wrapped_and_stringified_arguments_are_straightened(mcp):
    r = _data(await mcp.call_tool("list_projects", {"input": {}}))
    assert isinstance(r, list) and r[0]["slug"] == "default"
    r = _data(await mcp.call_tool("model_details", {"arguments": '{"air": "runware:101@1"}'}))
    assert r["air"] == FLUX
    r = _data(await mcp.call_tool("model_details", {"params": {"air": "runware:101@1"}}))
    assert r["air"] == FLUX
