from pathlib import Path

import httpx
import pytest

from vjhstudio.runware import docs_pages as D

FIX = Path(__file__).parent / "fixtures" / "docs"


def load(name: str) -> dict:
    return D.parse_docs((FIX / name).read_text(encoding="utf-8"))


def test_kling_dims_table_and_duration_rule():
    d = load("kling-4k.html")
    assert d["dims"] == [[3840, 2160], [2160, 3840], [2880, 2880]]
    assert d["dim_labels"]["3840x2160"] == "4K (16:9)"
    assert d["params"]["width"] == {"type": "integer", "required": True}
    assert d["params"]["duration"] == {
        "type": "integer",
        "min": 3,
        "max": 15,
        "step": 1,
        "default": 5,
    }
    assert "providerSettings.klingai.sound" in d["params"]
    assert [512, 512] not in d["dims"]


def test_ltx_rule_and_floats():
    d = load("ltx-2-3.html")
    assert d["dims"] == []
    assert d["params"]["width"] == {
        "type": "integer",
        "required": True,
        "min": 128,
        "max": 2048,
        "step": 64,
    }
    assert d["params"]["duration"] == {"type": "float", "required": True, "min": 1, "max": 20}
    assert d["params"]["fps"]["default"] == 25


def test_veo_allowed_values_and_optional_input():
    d = load("veo-3-1.html")
    assert d["params"]["duration"] == {"type": "integer", "default": 4, "values": [4, 6, 7, 8]}
    assert d["inputs"]["video"] == {"required": False}
    assert [1280, 720] in d["dims"] and d["dim_labels"]["1280x720"].startswith("720p")


def test_aleph_inputs():
    d = load("aleph-2-0.html")
    assert d["inputs"]["video"] == {"required": True}
    assert d["inputs"]["frameImages"] == {"required": False, "min_items": 1, "max_items": 2}
    assert "width" not in d["params"]


def test_flux_camelcase_names_come_from_the_heading_not_the_lowercased_id():
    d = load("flux-dev.html")
    assert "CFGScale" in d["params"]
    assert "numberResults" in d["params"]
    assert d["params"]["CFGScale"] == {"type": "float", "min": 0, "max": 20, "step": 0.01}
    assert d["params"]["numberResults"] == {
        "type": "integer",
        "min": 1,
        "max": 20,
        "default": 1,
    }
    assert "cfgscale" not in d["params"]
    assert "numberresults" not in d["params"]


def test_dotted_and_hyphenated_ids_parse_the_same():
    dotted = (
        '<dl class="component-APIParameter level-1" id="request-inputs.video">'
        '<dt><div class="header"><h3><a href="#request-inputs.video">video</a></h3>'
        '<div class="attributes"><span data-name="type">string</span>'
        '<span data-name="required">required</span></div></div></dt>'
        '<dd><p class="description">Input video.</p></dd></dl>'
    )
    hyphenated = (
        '<dl class="component-APIParameter level-0" id="request-inputs-video">'
        "<dt><span><code>inputs</code> » <code>video</code></span>"
        '<div class="header"><h3><a href="#request-inputs-video">video</a></h3>'
        '<div class="attributes"><span data-name="type">string</span>'
        '<span data-name="required">required</span></div></div></dt>'
        '<dd><p class="description">Input video.</p></dd></dl>'
    )
    assert D.parse_docs(dotted) == D.parse_docs(hyphenated)
    assert D.parse_docs(hyphenated)["inputs"]["video"] == {"required": True}


def test_parse_garbage_is_empty_not_error():
    assert D.parse_docs("<html><body>nothing</body></html>") == {
        "params": {},
        "inputs": {},
        "dims": [],
        "dim_labels": {},
    }


@pytest.mark.asyncio
async def test_fetch_uses_browser_ua_and_maps_404_to_none():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["ua"] = req.headers.get("user-agent")
        seen["url"] = str(req.url)
        return httpx.Response(404 if "missing" in str(req.url) else 200, text="<html></html>")

    t = httpx.MockTransport(handler)
    assert await D.fetch_docs_html("google-veo-3-1", transport=t) == "<html></html>"
    assert seen["url"] == "https://runware.ai/docs/models/google-veo-3-1"
    assert seen["ua"].startswith("Mozilla/5.0")
    assert await D.fetch_docs_html("missing-model", transport=t) is None


@pytest.mark.asyncio
async def test_fetch_raises_docs_error_on_server_failure():
    t = httpx.MockTransport(lambda r: httpx.Response(503))
    with pytest.raises(D.DocsError):
        await D.fetch_docs_html("x", transport=t)
