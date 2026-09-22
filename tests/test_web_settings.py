from runware import RunwareError

from vjhstudio import secrets


async def test_settings_page_lists_fields(client):
    r = await client.get("/settings")
    assert r.status_code == 200
    for key in ("jobs.concurrency", "ui.theme", "runware.transport"):
        assert f'name="{key}"' in r.text


async def test_save_settings(client):
    r = await client.post(
        "/settings",
        data={"jobs.concurrency": "4", "ui.theme": "daylight", "runware.transport": "rest"},
    )
    assert r.status_code == 200 and "Saved" in r.text
    r = await client.get("/settings")
    assert 'value="4"' in r.text


async def test_save_invalid_setting_422(client):
    r = await client.post("/settings", data={"jobs.concurrency": "many"})
    assert r.status_code == 422 and "integer" in r.text


async def test_api_key_save_masks_and_never_echoes(client, paths):
    r = await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    assert r.status_code == 200 and "••••••••1234" in r.text and "abcdefgh1234" not in r.text
    assert secrets.read_api_key(paths) == "abcdefgh1234"
    r = await client.post("/settings/api-key/clear")
    assert r.status_code == 200 and secrets.read_api_key(paths) is None


async def test_api_key_test_shows_balance(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["account_management"] = [[{"balance": 7.25, "usage": {}}]]  # live API shape
    r = await client.post("/settings/api-key/test")
    assert r.status_code == 200 and "$7.25" in r.text
    r = await client.get("/hx/header/balance")
    assert "$7.25" in r.text


async def test_api_key_test_failure_422(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["account_management"] = [RunwareError("invalidApiKey", "bad")]
    r = await client.post("/settings/api-key/test")
    assert r.status_code == 422 and "rejected the API key" in r.text


async def test_api_key_test_without_key_422(client):
    r = await client.post("/settings/api-key/test")
    assert r.status_code == 422 and "No API key" in r.text


async def test_settings_page_shows_key_guide(client):
    r = await client.get("/settings")
    assert "How to get a RunWare API key" in r.text and "https://runware.ai/signup" in r.text


async def test_clear_database_route_with_backup(client, paths):
    r = await client.post("/settings/database/clear", data={"backup_first": "on"})
    assert r.status_code == 200 and "Database cleared" in r.text and "pre-clear" in r.text


async def test_clear_database_route_without_backup(client):
    r = await client.post("/settings/database/clear", data={})
    assert r.status_code == 200 and "no backup taken" in r.text


async def test_settings_page_has_maintenance_section(client):
    r = await client.get("/settings")
    assert 'id="maintenance"' in r.text and "Clear database" in r.text


async def test_oob_balance_chip_is_a_top_level_node_and_appears_once(client, fake):
    """htmx 2.0 still swaps nested OOB elements but deprecates it; 3.0 will not."""
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["account_management"] = [
        [{"balance": {"amount": 7.25, "currency": "USD", "freeBalance": 0}}]
    ]
    r = await client.post("/settings/api-key/test")
    assert r.status_code == 200
    assert r.text.count('id="balance-chip"') == 1
    assert r.text.index('id="balance-chip"') > r.text.index("</form>")
    assert 'hx-swap-oob="true"' in r.text


async def test_settings_page_has_exactly_one_balance_chip(client, fake):
    fake.script["account_management"] = [
        [{"balance": {"amount": 7.25, "currency": "USD", "freeBalance": 0}}]
    ]
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    await client.post("/settings/api-key/test")
    r = await client.get("/settings")
    assert r.text.count('id="balance-chip"') == 1
    assert "hx-swap-oob" not in r.text


async def test_header_balance_refreshes_a_stale_cache(client, fake, app):
    from datetime import timedelta

    from vjhstudio import db
    from vjhstudio.models import utcnow
    from vjhstudio.services import meta

    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    with db.session_scope(app.state.boot.session_factory) as s:
        meta.set(s, "account.balance", "36.84")
        meta.set(s, "account.balance_at", (utcnow() - timedelta(hours=30)).isoformat())
    fake.script["account_management"] = [[{"balance": 27.9}]]
    r = await client.get("/hx/header/balance")
    assert r.status_code == 200 and "$27.90" in r.text and "$36.84" not in r.text
    # fresh now: the next poll shows the cache without asking again
    fake.script["account_management"] = []
    r = await client.get("/hx/header/balance")
    assert "$27.90" in r.text


async def test_header_balance_keeps_the_last_amount_when_runware_is_down(client, fake, app):
    from datetime import timedelta

    from runware import RunwareError

    from vjhstudio import db
    from vjhstudio.models import utcnow
    from vjhstudio.services import meta

    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    with db.session_scope(app.state.boot.session_factory) as s:
        meta.set(s, "account.balance", "12.5")
        meta.set(s, "account.balance_at", (utcnow() - timedelta(minutes=5)).isoformat())
    fake.script["account_management"] = [RunwareError("connectionFailed", "offline")]
    r = await client.get("/hx/header/balance")
    assert r.status_code == 200 and "$12.50 ?" in r.text and "last known" in r.text
