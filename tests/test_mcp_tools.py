"""The MCP tools, driven by the SDK's in-memory client.

Every test goes through a real ``Client`` so the argument schemas, the structured
returns and the refusals are exercised exactly as an agent host would see them.
A refusal arrives as ``is_error`` with the sentence inside
"Error executing tool <name>: ...", so the assertions look for a substring.
"""

import json

import pytest
from mcp.client import Client

from vjhstudio import db
from vjhstudio.mcp.server import MCPContext, build_server
from vjhstudio.models import CatalogModel, Job
from vjhstudio.services import settings

FLUX = "runware:101@1"
LTX = "lightricks:ltx@2.3"
FIRST_FRAME = "tests:firstframe@1"

IMAGE_REPLY = [{"imageURL": "http://x/1.png", "seed": 5, "cost": 0.004}]


@pytest.fixture
async def ctx(app, client):
    """``client`` keeps the app lifespan -- and therefore ``boot`` and the runner -- alive."""
    return MCPContext(
        session_factory=app.state.boot.session_factory,
        paths=app.state.paths,
        runner=lambda: app.state.runner,
        env=app.state.env,
        setting=app.state.setting,
        base_url="http://test",
    )


@pytest.fixture
async def mcp(ctx):
    async with Client(build_server(ctx)) as c:
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
    rows = _data(await mcp.call_tool("list_models", {"kind": "image"}))
    flux = next(r for r in rows if r["air"] == FLUX)
    assert flux["accepts"] == "seed image" and flux["price_usd"] > 0
    assert flux["unit"] == "per_image" and flux["needs_first_frame"] is False
    by_name = _data(await mcp.call_tool("list_models", {"kind": "image", "sort": "name"}))
    assert [r["name"].lower() for r in by_name] == sorted(r["name"].lower() for r in by_name)
    res = await mcp.call_tool("list_models", {"kind": "audio"})
    assert res.is_error and "image" in res.content[0].text


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
    res = await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "x", "project": "nope"})
    assert res.is_error and "list_projects" in res.content[0].text


async def test_cancel_job_and_unknown_job_ids(mcp, fake):
    fake.script["run"] = [IMAGE_REPLY]
    r = _data(await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "x"}))
    cancelled = _data(await mcp.call_tool("cancel_job", {"job_id": r["job_id"]}))
    assert cancelled["cancelled"] in (True, False)
    res = await mcp.call_tool("job_status", {"job_id": "nope"})
    assert res.is_error and "nope" in res.content[0].text


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
