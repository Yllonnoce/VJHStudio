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
THEME_JS = (REPO_ROOT / "vjhstudio" / "web" / "static" / "js" / "theme.js").read_text()
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


# ── What a swap and a history restore break: load-time initialisers ─────────────
# Every one of these used to run once, against the nodes that happened to exist then.
# A boosted swap replaces <main> (so <main>'s nodes are new), and a history restore
# replaces the whole body from a markup snapshot (so JS properties are gone). Each is
# now delegated from a node that survives both.


def test_lightbox_close_is_delegated_in_the_capture_phase():
    """`close` does not bubble, so a delegated listener has to catch it on the way
    down. Bound to the dialog instead, it would stop firing after the first boosted
    swap and leave <html> stuck with vjh-lightbox-open -- the page would never scroll
    again."""
    assert "document.addEventListener('DOMContentLoaded', () => {\n  const dlg" not in APP_JS
    close = APP_JS[APP_JS.index("document.addEventListener('close'") :]
    close = close[: close.index("\n\n")]
    assert "e.target.id === 'lightbox'" in close
    assert "classList.remove('vjh-lightbox-open')" in close
    assert close.rstrip().endswith("}, true);")  # capture phase


def test_no_lightbox_listener_is_bound_to_the_dialog_itself():
    """The rest of the lightbox (Esc, arrows, the dim-area click, close-lightbox)
    already looks the dialog up per event; nothing may go back to binding it."""
    assert "dlg.addEventListener" not in APP_JS


def test_the_queue_chips_highlight_is_owned_by_the_client():
    """The chip replaces itself every 10s with markup the server rendered for
    /hx/jobs/badge, and the server has no way of knowing which page that poll belongs
    to. It was once told, as `?at=<path>` on the poll URL -- but htmx reads hx-get once,
    when it processes the element, and closes over that string for the element's whole
    life (see the test below), so the parameter was frozen at the cold-load path and
    rewriting the attribute changed nothing. So JS owns the mark, and the settle runs
    after every swap rather than only after a navigation."""
    mark = APP_JS[APP_JS.index("window.vjhMarkCurrentNav = function ()") :]
    mark = mark[: mark.index("\n};")]
    code = "\n".join(ln for ln in mark.splitlines() if not ln.lstrip().startswith("//"))
    assert "jobs-badge" not in code and "'?at='" not in code  # no attribute rewriting
    assert "a[data-navlink]" in code  # the chip is one of these
    assert "document.addEventListener('htmx:afterSettle', settle);" in APP_JS
    for event in ("htmx:pushedIntoHistory", "htmx:historyRestore"):
        assert f"document.addEventListener('{event}', settleSoon)" in APP_JS
    assert "window.addEventListener('popstate', settleSoon)" in APP_JS
    # and from the cold load on, so the first poll is not the first time it runs
    assert "document.addEventListener('DOMContentLoaded', settle);" in APP_JS


def test_htmx_captures_a_polls_url_when_it_processes_the_element():
    """Why the `?at=` approach could not work, pinned in the vendored htmx 2.0.4: the
    trigger-wiring loop reads hx-<verb> into a local and hands *that* to every request
    the element ever issues. Changing the attribute afterwards is cosmetic."""
    wire = HTMX_JS[HTMX_JS.index("function wt(t,n,e)") :]
    wire = wire[: wire.index("function St(")]
    assert 'const o=te(t,"hx-"+r)' in wire  # read once, at process time
    assert "de(r,o,n,t)" in wire  # every request reuses that capture
    assert wire.index('const o=te(t,"hx-"+r)') < wire.index("de(r,o,n,t)")


async def test_the_badge_poll_url_carries_no_page_parameter(client):
    for path in ("/", "/gallery", "/queue"):
        assert 'hx-get="/hx/jobs/badge"' in (await client.get(path)).text
        assert "/hx/jobs/badge?" not in (await client.get(path)).text
    panel = (await client.get("/hx/jobs/active")).text
    assert 'hx-get="/hx/jobs/active"' in panel and "/hx/jobs/active?" not in panel


async def test_the_badge_endpoint_never_marks_anything_current(client):
    """Decision recorded: the endpoint marks from the *request* path and nothing else,
    and /hx/jobs/badge is never under /queue -- so a poll response is always unmarked,
    whatever page it was fired from. Only the cold-load page render marks the chip
    server-side (test below), and JS re-marks it after every swap."""
    for url in ("/hx/jobs/badge", "/hx/jobs/badge?at=/queue", "/hx/jobs/active"):
        assert 'aria-current="page"' not in (await client.get(url)).text


async def test_a_cold_load_of_queue_still_marks_the_chip_server_side(client):
    """Nothing here depends on JS having run: the page render knows its own path."""
    nav = _nav((await client.get("/queue")).text)
    marked = re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', nav)
    assert marked == ["/queue"]


def test_assets_dropzone_is_delegated_and_resolved_per_event():
    """A cold load of any other page has no #asset-upload-form at all, so the old
    load-time IIFE returned early and the dropzone was dead on every boosted visit
    to /assets."""
    zone = APP_JS[APP_JS.index("// ── Assets: dropzone drag/drop") :]
    zone = zone[: zone.index("// htmx dispatches htmx:xhr:progress")]
    assert (
        "const form = document.getElementById('asset-upload-form');\n  if (!form) return;"
        not in zone
    )
    assert "function dropzone(e)" in zone and "form.contains(e.target)" in zone
    for evt in ("dragenter", "dragover", "dragleave", "drop"):
        assert evt in zone
    assert "form.addEventListener" not in zone
    assert zone.count("document.addEventListener(evt") == 2
    assert "document.addEventListener('drop'" in zone


def test_theme_swatches_are_delegated_off_a_data_attribute():
    """A restored snapshot is markup: `btn.onclick = ...` does not survive it."""
    assert ".onclick" not in THEME_JS
    assert "e.target.closest('[data-vjh-swatch]')" in THEME_JS
    assert "vjhSetTheme(el.dataset.vjhSwatch)" in THEME_JS
    # rebuilt only when the restored markup carries no swatches at all
    assert "function _vjhBuildSwatches()" in THEME_JS
    assert "container.querySelector('[data-vjh-swatch]')) return;" in THEME_JS
    assert "document.addEventListener('htmx:historyRestore'" in THEME_JS


def test_boost_partial_warns_that_header_urls_are_not_refreshed():
    src = (TEMPLATES / "partials" / "_boost.html").read_text()
    assert "nothing in it is re-rendered by a swap" in src
    assert "hx-get" in src and "vjhMarkCurrentNav()" in src
