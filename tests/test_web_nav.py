"""Shared header navigation: a menu click swaps only <main>, so the top bar survives.

The bar is one template already (`_header.html`, included by `base.html`), but a full
page load threw it away and rebuilt it on every click. htmx's `hx-boost` keeps the very
same header nodes -- balance chip, queue chip, theme button, and their timers -- and
puts only the response's <main> in place. These tests pin the markup the swap needs and
the client-side bookkeeping that replaces what the server used to settle on its own."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "vjhstudio" / "web" / "templates"
APP_JS = (REPO_ROOT / "vjhstudio" / "web" / "static" / "js" / "app.js").read_text()
HTMX_JS = (REPO_ROOT / "vjhstudio" / "web" / "static" / "vendor" / "htmx.min.js").read_text()

BOOST_ATTRS = [
    'hx-boost="true"',
    'hx-target="main"',
    'hx-select="main"',
    'hx-swap="outerHTML show:window:top"',
    'hx-push-url="true"',
]


def _nav(html: str) -> str:
    return re.search(r"<nav>.*?</nav>", html, re.S).group(0)


async def test_every_header_link_is_boosted(client):
    nav = _nav((await client.get("/")).text)
    for label, href in [
        ("Home", "/"), ("Generate", "/generate"), ("Gallery", "/gallery"),
        ("Prompts", "/prompts"), ("Assets", "/assets"), ("Projects", "/projects"),
        ("Models", "/models"), ("Settings", "/settings"),
    ]:  # fmt: skip
        link = re.search(rf'<a href="{re.escape(href)}"[^>]*>{label}</a>', nav)
        assert link, label
        for attr in BOOST_ATTRS:
            assert attr in link.group(0), (label, attr)
    # the logo goes home the same way; the Queue chip lives in the same bar
    assert re.search(r'<a href="/"[^>]*hx-boost="true"[^>]*>VJHStudio</a>', nav)
    queue = re.search(r'<a href="/queue"[^>]*>', nav).group(0)
    for attr in BOOST_ATTRS:
        assert attr in queue, attr


async def test_polling_badges_do_not_inherit_the_boost_target(client):
    """The badges poll with their own hx-get. Were the boost attributes on the <ul>,
    every poll would inherit hx-target="main" and hx-push-url="true" -- swapping the
    page body with a chip and pushing /hx/jobs/badge into history. They sit on each
    <a> instead, so the <li>s stay clean."""
    nav = _nav((await client.get("/")).text)
    for badge_id in ("jobs-badge", "balance-chip"):
        li = re.search(rf'<li id="{badge_id}".*?>', nav, re.S).group(0)
        assert "hx-get=" in li
        assert "hx-target" not in li and "hx-push-url" not in li and "hx-select" not in li
    assert "<ul hx-boost" not in nav and "<nav hx-boost" not in nav


async def test_header_still_marks_the_current_page_server_side(client):
    """A cold load of a sub-page is a plain request: the macro must still mark it."""
    for path, label in [("/gallery", "Gallery"), ("/generate", "Generate")]:
        nav = _nav((await client.get(path)).text)
        marked = re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', nav)
        assert marked == [path], (path, marked)
        assert re.search(rf'aria-current="page">{label}</a>', nav)
    nav = _nav((await client.get("/")).text)
    # "/" marks Home once and only once -- never the logo, which has no data-navlink
    assert nav.count('aria-current="page"') == 1
    assert 'aria-current="page">Home</a>' in nav
    # /generate/video is still "Generate" (startsWith, not equality)
    nav = _nav((await client.get("/generate?mode=video")).text)
    assert 'aria-current="page">Generate</a>' in nav


async def test_only_the_links_the_macro_marks_carry_data_navlink(client):
    """app.js redoes the marking after a swap and keys off `data-navlink`, so the set
    has to be exactly the links the server-side rule applies to: the eight nav items
    and the Queue chip -- not the logo, and not the /settings chips."""
    nav = _nav((await client.get("/")).text)
    hrefs = re.findall(r'<a href="([^"]+)"[^>]*\bdata-navlink\b', nav)
    assert hrefs == [
        "/", "/generate", "/gallery", "/prompts", "/assets",
        "/projects", "/models", "/settings", "/queue",
    ]  # fmt: skip


async def test_boosted_request_gets_the_whole_page_and_htmx_selects_main(client):
    """No server change: htmx asks for the same URL and does the selecting itself."""
    r = await client.get("/gallery", headers={"HX-Request": "true", "HX-Boosted": "true"})
    assert r.status_code == 200
    assert "<main" in r.text and "</main>" in r.text
    assert "<title>" in r.text and "</head>" in r.text


async def test_htmx_reads_the_title_before_hx_select_narrows_the_response():
    """Verified against the vendored htmx 2.0.4: makeFragment() hangs the response's
    <title> on the fragment, swap() copies it to the swap context *before* the
    `if (select)` branch throws the rest away, and applies it after the swap unless
    ignoreTitle. So document.title follows a boosted click with no fallback in app.js."""
    swap = HTMX_JS[HTMX_JS.index("function $e(e,t,r,o)") :]
    swap = swap[: swap.index("function Je(")]
    assert "l.title=n.title" in swap
    assert swap.index("l.title=n.title") < swap.index("if(o.select)")
    assert "if(!r.ignoreTitle){kn(l.title)}" in swap
    # makeFragment: an "html" response keeps the <title> out of the stripped <head>
    assert "r.title=i.title" in HTMX_JS


async def test_swapped_main_carries_the_generate_page_scripts(client):
    """The swap replaces <main> wholesale, so everything the Generate page needs to
    boot -- Alpine's component root and the JSON blob it reads -- must live inside it.
    (htmx re-inserts scripts it swaps in, and Alpine's MutationObserver initialises
    x-data on any node that lands in the document.)"""
    r = await client.get("/generate", headers={"HX-Request": "true", "HX-Boosted": "true"})
    main = re.search(r"<main[^>]*>.*</main>", r.text, re.S).group(0)
    assert 'id="generate-initial"' in main
    assert 'id="generate-form"' in main and 'x-data="generateForm()"' in main


async def test_only_json_blobs_are_inline_scripts_inside_main():
    """Anything else inline under <main> re-runs on every swap, so it has to be
    idempotent. Today the only exception is the gallery's `?open=` opener, which is
    wrapped in an IIFE and guarded on readyState (below)."""
    offenders = []
    for tpl in TEMPLATES.rglob("*.html"):
        if tpl.name in ("base.html", "_header.html", "restarting.html"):
            continue
        for tag in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", tpl.read_text()):
            if 'type="application/json"' in tag:
                continue
            offenders.append((tpl.relative_to(TEMPLATES).as_posix(), tag))
    assert offenders == [("pages/gallery.html", "<script>")]


def test_gallery_open_script_does_not_wait_for_domcontentloaded_after_a_swap():
    src = (TEMPLATES / "pages" / "gallery.html").read_text()
    assert "if (document.readyState === 'loading') {" in src
    assert "document.addEventListener('DOMContentLoaded', vjhOpenRequestedCard);" in src
    assert "vjhOpenRequestedCard();" in src
    assert "}, 0);" in src  # the one-turn deferral survives either path


def test_app_js_redoes_the_current_link_and_the_bar_height_after_a_swap():
    assert "window.vjhMarkCurrentNav = function ()" in APP_JS
    assert "window.vjhMeasureHeader = function ()" in APP_JS
    # the server-side rule, to the letter
    assert "href === '/' ? path === '/' : (href !== '' && path.indexOf(href) === 0)" in APP_JS
    assert "a[data-navlink]" in APP_JS
    for event in ("htmx:pushedIntoHistory", "htmx:replacedInHistory", "htmx:historyRestore"):
        assert f"document.addEventListener('{event}', settleSoon)" in APP_JS
    assert "window.addEventListener('popstate', settleSoon)" in APP_JS
    assert "--vjh-header-h" in APP_JS


def test_generate_opts_out_of_the_history_snapshot():
    """Alpine's x-for chips would be generated on top of a restored snapshot, so the
    Generate page is refetched on Back instead of replayed."""
    base = (TEMPLATES / "base.html").read_text()
    assert '<main class="container"{% block main_attrs %}{% endblock %}>' in base
    generate = (TEMPLATES / "pages" / "generate.html").read_text()
    assert '{% block main_attrs %} hx-history="false"{% endblock %}' in generate
    assert "window.htmx.config.refreshOnHistoryMiss = true;" in APP_JS
    # and nowhere else: hx-history="false" anywhere in the document disables the
    # cache for the whole page, so exactly one template may carry it
    carriers = sorted(
        tpl.relative_to(TEMPLATES).as_posix()
        for tpl in TEMPLATES.rglob("*.html")
        if 'hx-history="false"' in tpl.read_text()
    )
    assert carriers == ["pages/generate.html"]


async def test_generate_page_main_carries_the_opt_out(client):
    r = await client.get("/generate")
    assert re.search(r'<main class="container" hx-history="false">', r.text)
    assert '<main class="container">' in (await client.get("/gallery")).text
