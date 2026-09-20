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
    fake.script["account_management"] = [
        [{"balance": {"amount": 7.25, "currency": "USD", "freeBalance": 0}}]
    ]
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
