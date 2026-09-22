"""The public docs page for a model (https://runware.ai/docs/models/<slug>) is the only
free source for enumerated durations, dimension tables and per-parameter ranges. This is
a pure parser plus a fetch helper; nothing here needs an API key."""

from __future__ import annotations

import html as htmllib
import re

import httpx

DOCS_BASE = "https://runware.ai/docs/models/"
USER_AGENT = "Mozilla/5.0 (compatible; VJHStudio/0.3; +https://github.com/yllonnoce/VJHStudio)"
_PARAM = re.compile(
    r'<dl class="component-APIParameter[^"]*" id="request-([^"]+)"[^>]*>(.*?)</dl>', re.S
)
# Real docs pages slugify every parameter id to an all-lowercase anchor
# (id="request-cfgscale", id="request-inputs-frameimages"), which loses the camelCase the
# RunWare API actually uses (CFGScale, frameImages, ...). The id is therefore never a
# reliable source for the name: nested rows carry the correctly-cased path in the
# breadcrumb right after <dt> (<code>inputs</code> » <code>frameImages</code>), and
# top-level rows carry it in their own heading (<h3><a href="#...">CFGScale</a></h3>).
# The id is only a last-resort fallback when neither is present.
_BREADCRUMB = re.compile(r'<dt[^>]*>\s*<span[^>]*>(.*?)</span>\s*<div class="header"', re.S)
_HEADING = re.compile(r"<h3[^>]*>\s*<a[^>]*>([^<]*)</a>", re.S)
_ATTR = re.compile(r'<span data-name="([^"]+)"[^>]*>([^<]*)</span>')
_ALLOWED = re.compile(r"Allowed values.*?<ul>(.*?)</ul>", re.S)
_CODE = re.compile(r"<code[^>]*>([^<]*)</code>")
_DIMS_BLOCK = re.compile(r'class="component-ModelDimensions".*?</table>', re.S)
_DIM_CELL = re.compile(r'<code class="dimension-value"[^>]*>([^<]*)</code>')
_WXH = re.compile(r"^(\d{2,5})x(\d{2,5})$")
EMPTY = {"params": {}, "inputs": {}, "dims": [], "dim_labels": {}}


class DocsError(RuntimeError):
    pass


def _num(text: str) -> int | float | str:
    t = text.strip()
    try:
        f = float(t)
    except ValueError:
        return t
    return int(f) if f.is_integer() else f


def _attrs(body: str) -> dict:
    out: dict = {}
    for name, raw in _ATTR.findall(body):
        val = htmllib.unescape(raw).strip()
        if name == "type":
            out["type"] = val
        elif name == "required":
            out["required"] = True
        elif name in ("min", "max", "step", "default"):
            out[name] = _num(val.split(":", 1)[1]) if ":" in val else _num(val)
        elif name == "items":
            m = re.match(r"(min|max)?\s*items:\s*(\d+)", val)
            if m:
                out[f"{m.group(1) or 'min'}_items"] = int(m.group(2))
    m = _ALLOWED.search(body)
    if m:
        vals = [_num(htmllib.unescape(c)) for c in _CODE.findall(m.group(1))]
        if vals:
            out["values"] = vals
    return out


def _dotted_name(raw_id: str, body: str) -> str:
    m = _BREADCRUMB.search(body)
    if m:
        segments = [htmllib.unescape(c).strip() for c in _CODE.findall(m.group(1))]
        if segments:
            return ".".join(segments)
    # A dot in the id means it already spells out the nested path in its original case
    # (the synthetic/legacy id="request-inputs.video" form, which has no breadcrumb to
    # confirm it) — trust it as-is rather than the heading, which would only give the
    # leaf ("video"), not the full path.
    if "." in raw_id:
        return raw_id.replace("-", ".")
    m = _HEADING.search(body)
    if m:
        heading = htmllib.unescape(m.group(1)).strip()
        if heading:
            return heading
    return raw_id.replace("-", ".")


def parse_docs(html: str) -> dict:
    params: dict = {}
    inputs: dict = {}
    for raw_id, body in _PARAM.findall(html):
        name = _dotted_name(raw_id, body)
        attrs = _attrs(body)
        if name.startswith("inputs."):
            inputs[name.split(".", 1)[1]] = {
                "required": bool(attrs.get("required")),
                **{k: v for k, v in attrs.items() if k in ("min_items", "max_items")},
            }
        else:
            params[name] = attrs
    dims: list[list[int]] = []
    labels: dict[str, str] = {}
    block = _DIMS_BLOCK.search(html)
    if block:
        cells = [htmllib.unescape(c).strip() for c in _DIM_CELL.findall(block.group(0))]
        pending: str | None = None
        for cell in cells:
            m = _WXH.match(cell.replace("×", "x"))
            if m:
                pair = [int(m.group(1)), int(m.group(2))]
                if pair not in dims:
                    dims.append(pair)
                if pending:
                    labels[f"{pair[0]}x{pair[1]}"] = pending
                pending = None
            else:
                pending = cell
    if not params and not inputs and not dims:
        return {"params": {}, "inputs": {}, "dims": [], "dim_labels": {}}
    return {"params": params, "inputs": inputs, "dims": dims, "dim_labels": labels}


async def fetch_docs_html(
    slug: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0
) -> str | None:
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as c:
            r = await c.get(DOCS_BASE + slug)
    except httpx.HTTPError as e:
        raise DocsError(f"docs page unreachable: {e}") from e
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise DocsError(f"docs page {r.status_code} for {slug}")
    return r.text
