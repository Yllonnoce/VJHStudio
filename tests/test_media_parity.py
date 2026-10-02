"""Music & SFX, Speech and 3D get what Image and Video already had: a place in the
Prompts library, a default model in Settings, a Reset button and a remembered form.
(The remembering itself is browser-side; the server's part is the markup it hangs on.)"""

from __future__ import annotations

from vjhstudio import db, models
from vjhstudio.services import catalog

ACE = "runware:ace-step@v1.5-turbo"
ACE_BASE = "runware:ace-step@v1.5-base"
XAI = "xai:tts@0"
HUNYUAN = "tencent:hunyuan-3d@3.1-rapid"


async def _key(client):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})


async def _music(client, app, fake, **extra):
    fake.script["run"] = [[{"audioURL": "http://x/a.mp3", "cost": 0.006}]]
    r = await client.post(
        "/generate/audio",
        data={
            "kind": "audio",
            "project_id": "1",
            "model": ACE,
            "prompt": "rain on a tin roof",
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    await app.state.runner.wait_idle()


# ---- home ---------------------------------------------------------------------------------
async def test_home_no_longer_singles_out_image_and_video(client):
    """Five kinds can be made now; two big cards for two of them said otherwise. The
    Generate link in the menu is the way in for all five."""
    text = (await client.get("/")).text
    assert (
        "hero-card" not in text and "Create an image" not in text and "Create a video" not in text
    )
    assert 'href="/generate/image"' not in text and 'href="/generate/video"' not in text
    assert "Recent" in text and "Spent today" in text and "Nothing generated yet." in text


# ---- the prompts library ------------------------------------------------------------------
async def test_a_music_submit_lands_in_the_prompts_library_and_loads_back(client, app, fake):
    await _key(client)
    await _music(client, app, fake, lyrics="[verse] drip drop", duration="45")
    with db.session_scope(app.state.boot.session_factory) as s:
        p = s.query(models.Prompt).filter_by(kind="audio").one()
        assert p.final_prompt == "rain on a tin roof" and p.title == "rain on a tin roof"
        assert p.form_json["lyrics"] == "[verse] drip drop" and p.form_json["duration"] == 45
        assert p.form_json["model"] == ACE and "project_id" not in p.form_json
        assert p.use_count == 1 and p.project_id == 1
        job = s.query(models.Job).filter_by(kind="audio").one()
        assert job.prompt_id == p.id
        pid = p.id
    # the same thing again is the same library row, used twice
    await _music(client, app, fake, lyrics="[verse] drip drop", duration="45")
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(models.Prompt).filter_by(kind="audio").count() == 1
        assert s.get(models.Prompt, pid).use_count == 2
    # it is listed on the Prompts page, filterable by its kind...
    page = (await client.get("/prompts?kind=audio")).text
    assert "rain on a tin roof" in page and '<option value="audio" selected>' in page
    for kind in ("audio", "speech", "3d"):
        assert f'<option value="{kind}"' in page
    # ...and "Load into form" opens the tab that made it, filled in
    r = await client.get(f"/generate?prompt={pid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/generate/audio?prompt={pid}"
    form = (await client.get(f"/generate/audio?prompt={pid}")).text
    assert ">rain on a tin roof</textarea>" in form and ">[verse] drip drop</textarea>" in form
    assert 'value="45"' in form and f'<option value="{ACE}"' in form
    assert (await client.get(f"/generate/speech?prompt={pid}")).status_code == 404


async def test_speech_and_3d_submits_are_remembered_too(client, app, fake):
    await _key(client)
    fake.script["run"] = [
        [{"audioURL": "http://x/s.mp3"}],
        [{"outputs": {"files": [{"url": "http://x/m.glb"}]}}],
    ]
    await client.post(
        "/generate/speech",
        data={
            "kind": "speech",
            "project_id": "1",
            "model": XAI,
            "text": "Welcome aboard.",
            "voice": "luna",
        },
    )
    await app.state.runner.wait_idle()
    await client.post(
        "/generate/3d",
        data={
            "kind": "3d",
            "project_id": "1",
            "model": HUNYUAN,
            "source": "text",
            "prompt": "a brass telescope",
        },
    )
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        speech = s.query(models.Prompt).filter_by(kind="speech").one()
        assert speech.final_prompt == "Welcome aboard." and speech.form_json["voice"] == "luna"
        obj = s.query(models.Prompt).filter_by(kind="3d").one()
        assert obj.final_prompt == "a brass telescope"
        sid = speech.id
    form = (await client.get(f"/generate/speech?prompt={sid}")).text
    assert (
        ">Welcome aboard.</textarea>" in form
        and '<option value="luna" selected>luna</option>' in form
    )


# ---- default model per tab ------------------------------------------------------------------
async def test_each_new_tab_has_a_default_model_setting(client, app):
    page = (await client.get("/settings")).text
    for key in ("defaults.audio_model", "defaults.speech_model", "defaults.model3d_model"):
        assert f'name="{key}"' in page, key
    assert f'<option value="{ACE_BASE}"' in page  # a real dropdown of that kind's models
    r = await client.post(
        "/settings",
        data={"_form": "general", "defaults.audio_model": ACE_BASE},
    )
    assert r.status_code == 200
    tab = (await client.get("/generate/audio")).text
    selected = tab[tab.index(f'<option value="{ACE_BASE}"') :][:200]
    assert "selected" in selected.split(">")[0]


# ---- reset, and the hooks the remembered form hangs on ----------------------------------------
async def test_the_new_tabs_have_reset_and_the_markers_the_draft_script_needs(client, app, fake):
    for kind in ("audio", "speech", "3d"):
        page = (await client.get(f"/generate/{kind}")).text
        assert 'id="media-reset"' in page and ">Reset</button>" in page, kind
        form = page[page.index('<form id="media-form"') :][:400]
        assert f'data-draft-kind="{kind}"' in form and 'data-prefilled="0"' in form
    # a page opened from a remix or a saved prompt must not be overwritten by a draft
    await _key(client)
    await _music(client, app, fake)
    with db.session_scope(app.state.boot.session_factory) as s:
        oid = s.query(models.Output).one().id
        pid = s.query(models.Prompt).one().id
    for url in (f"/generate/audio?remix={oid}", f"/generate/audio?prompt={pid}"):
        page = (await client.get(url)).text
        assert 'data-prefilled="1"' in page[page.index('<form id="media-form"') :][:400], url
    js = (await client.get("/static/js/app.js")).text
    assert "vjh.media.draft." in js and "media-reset" in js


def test_the_default_models_exist_in_the_shipped_catalog(tmp_path):
    from vjhstudio import boot, config
    from vjhstudio.services import settings as settings_svc

    f = boot.boot(config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})).session_factory
    with db.session_scope(f) as s:
        for key, kind in (
            ("defaults.audio_model", "audio"),
            ("defaults.speech_model", "speech"),
            ("defaults.model3d_model", "3d"),
        ):
            air = settings_svc.get(s, key)
            assert air in [m.air for m in catalog.list_generate_models(s, kind)], key


async def test_the_3d_tab_can_upload_a_picture_without_leaving_it(client, app):
    """Video can upload its first frame from the Generate page; 3D sent you to Assets."""
    page = (await client.get("/generate/3d")).text
    assert "data-media-upload" in page and 'type="file"' in page and 'accept="image/*"' in page
    js = (await client.get("/static/js/app.js")).text
    assert "data-media-upload" in js and "/assets/upload" in js
