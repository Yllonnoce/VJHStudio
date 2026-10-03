"""A model's reply, as safe HTML. The text is untrusted: raw HTML in it is shown rather
than run, and it cannot make the browser fetch anything (no images) or follow a
``javascript:`` link (markdown-it refuses those schemes)."""

from __future__ import annotations

from markdown_it import MarkdownIt
from markupsafe import Markup

_md = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")


def _link_open(renderer, tokens, idx, options, env):
    tokens[idx].attrSet("target", "_blank")
    tokens[idx].attrSet("rel", "noopener noreferrer")
    return renderer.renderToken(tokens, idx, options, env)


_md.add_render_rule("link_open", _link_open)


def render(text: str) -> Markup:
    # markdown-it escapes everything it did not generate itself (html is off above)
    return Markup(_md.render(text or ""))  # noqa: S704
