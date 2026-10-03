"""The Chat page's data, its RunWare task and the reply itself (no HTTP here).

What RunWare does was checked live on 2026-10-03 (see the spec): pictures are a
task-level list that is forgotten between turns, a small ``maxTokens`` lets a reasoning
model return nothing, and a model that cannot see refuses ``inputs.images``."""

from __future__ import annotations

import asyncio

import pytest
from runware import RunwareError

from tests.fakes.fake_runware import FakeRunware, fake_factory
from vjhstudio import config, db
from vjhstudio.models import ChatMessage, UsageEntry
from vjhstudio.runware import tasks_chat
from vjhstudio.services import assets, catalog, chat, markdown, migrate

HAIKU = "anthropic:claude@haiku-4.5"
GLM = "zai:glm@5.1"


@pytest.fixture
def paths(tmp_path):
    return config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "data")})


@pytest.fixture
def factory(paths):
    config.ensure_dirs(paths)
    migrate.upgrade(paths.db)
    f = db.make_session_factory(db.make_engine(paths.db))
    with db.session_scope(f) as s:
        for air, name, caps in (
            (HAIKU, "Claude Haiku 4.5", ["io:text-to-text", "io:image-to-text", "form:checkpoint"]),
            (GLM, "GLM-5.1", ["io:text-to-text", "form:checkpoint"]),
            ("runware:152@1", "Qwen2.5-VL", ["io:image-to-text", "op:caption"]),
            ("runware:llama@pe", "Prompt Enhancer", ["io:text-to-text", "op:prompt-enhance"]),
            ("acme:found@1", "Found By Search", []),
        ):
            catalog.upsert_row(
                s,
                {"air": air, "name": name, "kind": "text", "capabilities": caps,
                 "price": {"unit": "per_1m_tokens", "primary": None, "tiers": {}}},
                source="search",
            )  # fmt: skip
    return f


def _deps(factory, paths, fake):
    return chat.ReplyDeps(factory, paths, fake_factory(fake), "key", "rest")


def _png(colour=(200, 20, 20)) -> bytes:
    import io

    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(b, "PNG")
    return b.getvalue()


def _asset(factory, paths, colour=(200, 20, 20)) -> int:
    with db.session_scope(factory) as s:
        a, _ = assets.store_upload(
            s, paths, original_name="p.png", content=_png(colour), mime="image/png"
        )
        return a.id


def _start(factory, text="Hello", model=HAIKU, asset_ids=(), instruction="") -> int:
    with db.session_scope(factory) as s:
        c = chat.create(s, title=chat.title_from(text), model_air=model, system_prompt=instruction)
        chat.add_user_message(s, c, text, list(asset_ids))
        return c.id


async def _reply(factory, paths, fake, chat_id, cancel=None, on_event=None):
    events = []

    def emit(kind, payload):
        events.append((kind, payload))
        if on_event:
            on_event(kind, payload)

    mid = await chat.run_reply(
        _deps(factory, paths, fake), chat_id, emit, cancel or asyncio.Event()
    )
    with db.session_scope(factory) as s:
        m = s.get(ChatMessage, mid)
        s.expunge(m)
    return m, events


# ---- the task ---------------------------------------------------------------------------
def test_task_has_history_instruction_and_no_max_tokens():
    msgs = [{"role": "user", "content": "Hi"}]
    t = tasks_chat.build_chat_task(HAIKU, msgs, "u-1", system_prompt="  Be brief.  ")
    assert t == {
        "taskType": "textInference",
        "taskUUID": "u-1",
        "model": HAIKU,
        "messages": msgs,
        "includeCost": True,
        "includeUsage": True,
        "settings": {"systemPrompt": "Be brief."},
    }
    # nothing to say: no empty settings object, and never a maxTokens (a reasoning model
    # given a small one spends it all thinking and returns no text)
    bare = tasks_chat.build_chat_task(HAIKU, msgs, "u-2")
    assert "settings" not in bare and "inputs" not in bare and "maxTokens" not in str(bare)


def test_pictures_ride_on_the_task_not_the_message():
    t = tasks_chat.build_chat_task(
        HAIKU, [{"role": "user", "content": "Look"}], "u", images=["uuid-a", "uuid-b"]
    )
    assert t["inputs"] == {"images": ["uuid-a", "uuid-b"]}
    assert t["messages"] == [{"role": "user", "content": "Look"}]


# ---- history ------------------------------------------------------------------------------
def _m(role, content, error=None, asset_ids=None):
    return ChatMessage(role=role, content=content, error=error, asset_ids_json=asset_ids or [])


def test_history_skips_failed_replies_and_joins_a_retried_message():
    msgs = [
        _m("user", "One"),
        _m("assistant", "First answer"),
        _m("user", "Two"),
        _m("assistant", "half an ans", error="RunWare went away"),
        _m("assistant", ""),
        _m("user", "Two, again"),
    ]
    assert chat.history(msgs) == [
        {"role": "user", "content": "One"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Two\n\nTwo, again"},
    ]


def test_picture_ids_are_every_picture_of_the_conversation_once_in_order():
    msgs = [
        _m("user", "a", asset_ids=[5, 2]),
        _m("assistant", "ok"),
        _m("user", "b", asset_ids=[2, 9]),
    ]
    assert chat.picture_ids(msgs) == [5, 2, 9]


def test_title_is_the_first_line():
    assert chat.title_from("  \n Plan my week \n in detail") == "Plan my week"
    assert chat.title_from("x" * 200) == "x" * 60
    assert chat.title_from("   ") == "New chat"


# ---- which models -----------------------------------------------------------------------------
def test_chat_models_are_the_conversational_text_models(factory):
    with db.session_scope(factory) as s:
        rows = chat.chat_models(s)
        airs = {r["air"]: r["vision"] for r in rows}
        # captioners and the prompt enhancer share the Text kind but hold no conversation;
        # a model found by search has no tags yet and is let through
        assert airs == {HAIKU: True, GLM: False, "acme:found@1": False}
        catalog.get_by_air(s, GLM).is_hidden = True
        s.flush()
        assert GLM not in [r["air"] for r in chat.chat_models(s)]


# ---- Markdown ------------------------------------------------------------------------------------
def test_markdown_renders_inert():
    html = str(
        markdown.render(
            "# Title\n\n**bold** and `code`\n\n<script>alert(1)</script>\n\n"
            "[bad](javascript:alert(1)) [good](https://example.com)\n\n"
            "![pic](https://tracker.example/p.png)\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
            "```python\nprint('<b>')\n```"
        )
    )
    assert "<strong>bold</strong>" in html and "<code>code</code>" in html and "<table>" in html
    assert "<script" not in html and "&lt;script&gt;" in html  # shown, not run
    assert 'href="javascript' not in html
    assert '<a href="https://example.com" target="_blank" rel="noopener noreferrer">' in html
    assert "<img" not in html  # a reply cannot make the browser fetch a remote picture
    assert "print('&lt;b&gt;')" in html or "print(&#x27;&lt;b&gt;&#x27;)" in html


# ---- the reply ---------------------------------------------------------------------------------------
async def test_reply_is_saved_with_cost_and_usage_and_counted_in_spending(factory, paths):
    fake = FakeRunware(
        {"stream": [{"pieces": ["Hel", "lo!"], "cost": 0.0012, "finish": "stop",
                     "usage": {"promptTokens": 31, "completionTokens": 19}}]}
    )  # fmt: skip
    cid = _start(factory, "Say hello", instruction="Be brief.")
    m, events = await _reply(factory, paths, fake, cid)
    assert [e for e in events if e[0] == "delta"] == [
        ("delta", {"text": "Hel"}),
        ("delta", {"text": "lo!"}),
    ]
    assert events[-1] == ("done", {"message_id": m.id})
    assert (m.role, m.content, m.error, m.model_air) == ("assistant", "Hello!", None, HAIKU)
    assert (m.cost, m.prompt_tokens, m.completion_tokens, m.finish_reason) == (
        0.0012,
        31,
        19,
        "stop",
    )
    task = [p for n, p in fake.calls if n == "stream"][0]
    assert task["messages"] == [{"role": "user", "content": "Say hello"}]
    assert task["settings"] == {"systemPrompt": "Be brief."} and "inputs" not in task
    with db.session_scope(factory) as s:
        u = s.query(UsageEntry).one()
        assert (u.task_type, u.model_air, u.cost) == ("textInference", HAIKU, 0.0012)
        assert chat.total_cost(s, cid) == 0.0012


async def test_an_empty_reply_is_an_error(factory, paths):
    """GPT-5 nano, live: all tokens spent on reasoning, ``finishReason: length``, no text.
    A blank bubble would look like a hang; it is billed, so the cost is still counted."""
    fake = FakeRunware({"stream": [{"pieces": [], "cost": 0.0001, "finish": "length"}]})
    m, _ = await _reply(factory, paths, fake, _start(factory))
    assert m.content == "" and "ran out of room" in m.error and m.cost == 0.0001
    with db.session_scope(factory) as s:
        assert s.query(UsageEntry).one().cost == 0.0001


async def test_a_runware_error_is_saved_on_the_reply(factory, paths):
    fake = FakeRunware({"stream": [RunwareError("invalidApiKey", "bad key")]})
    m, events = await _reply(factory, paths, fake, _start(factory))
    assert "rejected the API key" in m.error and m.content == "" and m.cost is None
    assert events == [("done", {"message_id": m.id})]
    # ...and one that breaks half way keeps what arrived, still marked as failed
    fake = FakeRunware(
        {"stream": [{"pieces": ["Half an "], "error": RunwareError("timeout", "slow")}]}
    )
    m, _ = await _reply(factory, paths, fake, _start(factory))
    assert m.content == "Half an " and m.error
    with db.session_scope(factory) as s:
        assert s.query(UsageEntry).count() == 0


async def test_stop_keeps_what_was_written(factory, paths):
    """The browser going away (Stop, or closing the page) sets the cancel flag."""
    fake = FakeRunware({"stream": [{"pieces": ["One. ", "Two. ", "Three."], "cost": 0.5}]})
    cancel = asyncio.Event()
    m, _ = await _reply(
        factory, paths, fake, _start(factory), cancel, on_event=lambda k, p: cancel.set()
    )
    assert (m.content, m.finish_reason, m.error, m.cost) == ("One. ", "stopped", None, None)
    # a stopped reply is part of the conversation from then on
    with db.session_scope(factory) as s:
        c = chat.get(s, m.chat_id)
        assert chat.history(c.messages)[-1] == {"role": "assistant", "content": "One. "}


async def test_pictures_are_uploaded_once_and_sent_every_turn(factory, paths):
    a, b = _asset(factory, paths), _asset(factory, paths, (0, 0, 250))
    fake = FakeRunware(
        {
            "media_storage": [[{"mediaUUID": "uuid-a"}], [{"mediaUUID": "uuid-b"}]],
            "stream": [{"pieces": ["Red."]}, {"pieces": ["Still red."]}],
        }
    )
    cid = _start(factory, "What colour?", asset_ids=[a, b])
    await _reply(factory, paths, fake, cid)
    with db.session_scope(factory) as s:
        chat.add_user_message(s, chat.get(s, cid), "And now?", [])
    await _reply(factory, paths, fake, cid)
    first, second = [p for n, p in fake.calls if n == "stream"]
    assert first["inputs"] == {"images": ["uuid-a", "uuid-b"]}
    assert second["inputs"] == {"images": ["uuid-a", "uuid-b"]}  # not remembered by RunWare
    assert len([1 for n, _ in fake.calls if n == "media_storage"]) == 2  # cached after that
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]


async def test_a_deleted_picture_is_skipped(factory, paths):
    a = _asset(factory, paths)
    cid = _start(factory, "Look", asset_ids=[a, 9999])
    with db.session_scope(factory) as s:
        assets.delete(s, paths, a)
    fake = FakeRunware({"stream": [{"pieces": ["I see nothing."]}]})
    m, _ = await _reply(factory, paths, fake, cid)
    assert m.error is None and "inputs" not in [p for n, p in fake.calls if n == "stream"][0]


async def test_a_text_only_model_is_not_sent_pictures(factory, paths):
    """GLM refuses ``inputs.images`` outright; the pictures stay in the conversation for
    when the model is switched back."""
    a = _asset(factory, paths)
    fake = FakeRunware({"stream": [{"pieces": ["ok"]}]})
    m, _ = await _reply(factory, paths, fake, _start(factory, "Look", model=GLM, asset_ids=[a]))
    assert m.error is None and "inputs" not in [p for n, p in fake.calls if n == "stream"][0]
    assert not [1 for n, _ in fake.calls if n == "media_storage"]


async def test_a_picture_that_will_not_upload_fails_the_reply_cleanly(factory, paths):
    a = _asset(factory, paths)
    fake = FakeRunware({"media_storage": [RunwareError("invalidApiKey", "bad")]})
    m, _ = await _reply(factory, paths, fake, _start(factory, "Look", asset_ids=[a]))
    assert m.error and not [1 for n, _ in fake.calls if n == "stream"]


def test_deleting_a_chat_deletes_its_messages(factory):
    cid = _start(factory)
    with db.session_scope(factory) as s:
        assert chat.delete(s, cid) and not chat.delete(s, cid)
    with db.session_scope(factory) as s:
        assert s.query(ChatMessage).count() == 0 and chat.list_chats(s) == []


def test_rename_and_list_order(factory):
    one, two = _start(factory, "First"), _start(factory, "Second")
    with db.session_scope(factory) as s:
        assert [c.id for c in chat.list_chats(s)] == [two, one]
        assert chat.rename(s, one, "  Holiday plans  ").title == "Holiday plans"
        assert chat.rename(s, one, "   ").title == "Holiday plans"  # a blank name changes nothing
        with pytest.raises(LookupError):
            chat.rename(s, 999, "x")
