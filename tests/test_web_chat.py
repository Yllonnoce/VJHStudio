"""The Chat page over HTTP: pages, the streamed send, and the conversation list."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image
from runware import RunwareError

from vjhstudio import db
from vjhstudio.models import Chat, ChatMessage, Output, UsageEntry
from vjhstudio.services import catalog
from vjhstudio.services import chat as chat_svc

HAIKU = "anthropic:claude@haiku-4.5"  # ships with the app; sees pictures
GLM = "zai:glm@5.1"  # text only: cannot be shown pictures


@pytest.fixture(autouse=True)
async def _text_only_model(client, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        catalog.upsert_row(
            s,
            {"air": GLM, "name": "GLM-5.1", "kind": "text", "capabilities": ["io:text-to-text"],
             "price": {"unit": "per_1m_tokens", "primary": 5.8, "tiers": {}}},
            source="content",
        )  # fmt: skip


async def _key(client):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def _streams(fake):
    return [p for n, p in fake.calls if n == "stream"]


async def _send(client, **data):
    data.setdefault("model", HAIKU)
    return await client.post("/chat/send", data=data)


def _png(colour=(10, 200, 10)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(b, "PNG")
    return b.getvalue()


# ---- the page ------------------------------------------------------------------------------
async def test_menu_has_chat_and_the_empty_page_offers_the_chat_models(client):
    page = (await client.get("/chat")).text
    assert '<a href="/chat" data-navlink' in page
    assert 'id="chat-form"' in page and 'name="message"' in page
    # Haiku is the default; a model that cannot see pictures says so to the page script
    assert f'<option value="{HAIKU}" data-vision="1" selected>' in page
    assert f'<option value="{GLM}" data-vision="0">' in page
    assert "No conversations yet" in page
    assert (await client.get("/chat/999")).status_code == 404


# ---- sending -------------------------------------------------------------------------------
async def test_first_message_creates_the_chat_and_streams(client, app, fake):
    await _key(client)
    fake.script["stream"] = [{"pieces": ["Hello ", "**there**"], "cost": 0.0012}]
    r = await _send(client, message="Say hello\nplease", system_prompt="Be brief.")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert [e[0] for e in events] == ["start", "delta", "delta", "done"]
    start, done = events[0][1], events[-1][1]
    assert start["new"] is True and start["url"] == f"/chat/{start['chat_id']}"
    assert start["title"] == "Say hello" and "Say hello\nplease" in start["user_html"]
    assert events[1][1] == {"text": "Hello "}
    assert "<strong>there</strong>" in done["html"] and "$0.0012" in done["html"]
    assert done["total"] == "$0.0012" and done["retry"] is False
    assert _streams(fake)[0]["settings"] == {"systemPrompt": "Be brief."}
    with db.session_scope(app.state.boot.session_factory) as s:
        c = s.query(Chat).one()
        assert (c.title, c.model_air, c.system_prompt) == ("Say hello", HAIKU, "Be brief.")
        assert [(m.role, m.content) for m in c.messages] == [
            ("user", "Say hello\nplease"),
            ("assistant", "Hello **there**"),
        ]
        assert s.query(UsageEntry).one().cost == 0.0012


async def test_second_message_sends_the_history_and_can_switch_model(client, app, fake):
    await _key(client)
    fake.script["stream"] = [{"pieces": ["One."]}, {"pieces": ["Two."]}]
    cid = _events((await _send(client, message="First")).text)[0][1]["chat_id"]
    r = await _send(client, chat_id=cid, message="Second", model=GLM)
    start = _events(r.text)[0][1]
    assert start["chat_id"] == cid and start["new"] is False
    second = _streams(fake)[1]
    assert second["model"] == GLM and second["messages"] == [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "One."},
        {"role": "user", "content": "Second"},
    ]
    with db.session_scope(app.state.boot.session_factory) as s:
        c = s.query(Chat).one()
        assert c.model_air == GLM and [m.model_air for m in c.messages] == [
            None,
            HAIKU,
            None,
            GLM,
        ]


async def test_a_send_is_refused_before_anything_is_saved_or_sent(client, app, fake):
    r = await _send(client, message="Hi")
    assert r.status_code == 422 and "API key" in r.json()["error"]
    await _key(client)
    for data, status, words in (
        ({"message": "   "}, 422, "Write a message"),
        ({"message": "Hi", "model": "nope:1@1"}, 422, "Choose a model"),
        ({"message": "Hi", "model": "runware:101@1"}, 422, "Choose a model"),  # an image model
        ({"message": "Hi", "chat_id": "999"}, 404, "no longer exists"),
        ({"retry": "1", "chat_id": "999"}, 404, "no longer exists"),
    ):
        r = await _send(client, **data)
        assert r.status_code == status and words in r.json()["error"], data
    assert _streams(fake) == []
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(Chat).count() == 0 and s.query(ChatMessage).count() == 0


async def test_message_text_is_escaped(client, fake):
    await _key(client)
    fake.script["stream"] = [{"pieces": ["<img src=x onerror=alert(1)>"]}]
    events = _events((await _send(client, message="<script>alert(1)</script>")).text)
    assert (
        "<script>" not in events[0][1]["user_html"]
        and "&lt;script&gt;" in events[0][1]["user_html"]
    )
    assert "<img" not in events[-1][1]["html"]
    page = (await client.get(events[0][1]["url"])).text
    assert "<script>alert(1)</script>" not in page and "<img src=x" not in page


async def test_retry_sends_the_message_once(client, app, fake):
    await _key(client)
    fake.script["stream"] = [RunwareError("timeout", "slow"), {"pieces": ["Here you go."]}]
    events = _events((await _send(client, message="Tell me a joke")).text)
    cid, done = events[0][1]["chat_id"], events[-1][1]
    assert done["retry"] is True and "Try again" in done["html"] and "error" in done["html"]
    r = await _send(client, chat_id=cid, retry="1")
    events = _events(r.text)
    assert events[0][1]["user_html"] == "" and events[-1][1]["retry"] is False
    assert _streams(fake)[1]["messages"] == [{"role": "user", "content": "Tell me a joke"}]
    with db.session_scope(app.state.boot.session_factory) as s:
        assert [(m.role, m.error) for m in s.query(Chat).one().messages] == [
            ("user", None),
            ("assistant", None),
        ]
    # nothing is waiting any more: a second "try again" is refused, not billed
    r = await _send(client, chat_id=cid, retry="1")
    assert r.status_code == 409 and len(_streams(fake)) == 2


# ---- pictures ---------------------------------------------------------------------------------
async def test_pictures_are_attached_shown_and_sent(client, app, fake):
    await _key(client)
    up = await client.post(
        "/assets/upload",
        files={"files": ("cat.png", _png(), "image/png")},
        headers={"Accept": "application/json"},
    )
    aid = up.json()["assets"][0]["id"]
    fake.script["media_storage"] = [[{"mediaUUID": "uuid-cat"}]]
    fake.script["stream"] = [{"pieces": ["A green square."]}]
    r = await client.post(
        "/chat/send",
        data={"model": HAIKU, "message": "What is this?", "asset_ids": [str(aid), "junk"]},
    )
    events = _events(r.text)
    assert "/files/asset-thumbs/" in events[0][1]["user_html"]
    assert _streams(fake)[0]["inputs"] == {"images": ["uuid-cat"]}
    page = (await client.get(events[0][1]["url"])).text
    assert "/files/asset-thumbs/" in page and "A green square." in page


async def test_picker_offers_assets_and_gallery_pictures(client, app):
    await client.post(
        "/assets/upload",
        files={"files": ("cat.png", _png(), "image/png")},
        headers={"Accept": "application/json"},
    )
    r = await client.get("/hx/chat/picker")
    assert r.status_code == 200 and 'data-chat-asset="' in r.text and "cat.png" in r.text
    assert (await client.post("/chat/attach-output/999")).status_code == 404


# ---- the list ---------------------------------------------------------------------------------
async def test_saved_page_list_rename_and_delete(client, app, fake):
    await _key(client)
    fake.script["stream"] = [
        {"pieces": ["# Plan\n\n- one"], "cost": 0.002},
        {"pieces": ["ok"], "cost": 0.001},
    ]
    one = _events((await _send(client, message="Plan my week", system_prompt="Short.")).text)[0][1]
    two = _events((await _send(client, message="Other thing")).text)[0][1]
    page = (await client.get(one["url"])).text
    assert "<h1>Plan</h1>" in page and "<li>one</li>" in page and "$0.0020" in page
    assert ">Short.</textarea>" in page and f'name="chat_id" value="{one["chat_id"]}"' in page
    assert page.index("Other thing") < page.index("Plan my week</a>")  # newest first in the list
    assert f'href="{one["url"]}" aria-current="page"' in page

    r = await client.post(f"/chat/{one['chat_id']}/rename", data={"title": "Week <plan>"})
    assert r.status_code == 200 and "Week &lt;plan&gt;" in r.text
    assert (await client.post("/chat/999/rename", data={"title": "x"})).status_code == 404
    lst = (await client.get(f"/hx/chat/list?current={two['chat_id']}")).text
    assert "Week &lt;plan&gt;" in lst and f'href="{two["url"]}" aria-current="page"' in lst

    assert (await client.post(f"/chat/{one['chat_id']}/delete")).status_code == 200
    assert (await client.post(f"/chat/{one['chat_id']}/delete")).status_code == 404
    assert (await client.get(one["url"])).status_code == 404
    with db.session_scope(app.state.boot.session_factory) as s:
        assert [c.id for c in chat_svc.list_chats(s)] == [two["chat_id"]]
        assert s.query(UsageEntry).count() == 2  # spending history outlives the conversation


async def test_thread_partial_shows_a_stopped_reply_and_try_again(client, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        c = chat_svc.create(s, title="T", model_air=HAIKU)
        chat_svc.add_user_message(s, c, "Go on", [])
        s.add(
            ChatMessage(
                chat_id=c.id,
                role="assistant",
                content="Once upon",
                finish_reason="stopped",
                model_air=HAIKU,
            )
        )
        chat_svc.add_user_message(s, c, "And?", [])
        cid = c.id
    r = await client.get(f"/hx/chat/{cid}/thread")
    assert r.status_code == 200 and "Once upon" in r.text and "stopped" in r.text
    assert "Try again" in r.text  # the last message never got its reply


# ---- settings -----------------------------------------------------------------------------------
async def test_settings_offers_a_default_chat_model(client, app):
    page = (await client.get("/settings")).text
    assert 'name="defaults.chat_model"' in page
    r = await client.post("/settings", data={"_form": "general", "defaults.chat_model": GLM})
    assert r.status_code == 200
    assert f'<option value="{GLM}" data-vision="0" selected>' in (await client.get("/chat")).text
    # a default that has since been hidden falls back to the first model on the list
    with db.session_scope(app.state.boot.session_factory) as s:
        catalog.get_by_air(s, GLM).is_hidden = True
    assert (
        "selected"
        in (await client.get("/chat")).text.split('name="model"')[1].split("</select>")[0]
    )


async def test_a_gallery_picture_can_be_attached(client, app, fake):
    """Picking from the Gallery imports the picture into Assets (once: the import is
    deduplicated by content, as "Use as reference" is) and answers like an upload."""
    await _key(client)
    fake.script["run"] = [[{"imageURL": "http://x/0.png", "seed": 1, "cost": 0.001}]]
    form = {"project_id": "1", "model": "runware:101@1", "subject": "castle", "width": "1024",
            "height": "1024", "number_results": "1", "output_format": "PNG"}  # fmt: skip
    await client.post("/generate/image", data=form)
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        oid = s.query(Output).one().id
    picker = (await client.get("/hx/chat/picker")).text
    assert f'data-chat-output="{oid}"' in picker and "/files/thumbs/" in picker
    first = (await client.post(f"/chat/attach-output/{oid}")).json()
    again = (await client.post(f"/chat/attach-output/{oid}")).json()
    assert first == again and first["thumb_url"].startswith("/files/asset-thumbs/")
    assert f'data-chat-asset="{first["id"]}"' in (await client.get("/hx/chat/picker")).text
