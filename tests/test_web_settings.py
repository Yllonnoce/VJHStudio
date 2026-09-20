from runware import RunwareError
from runwarestudio import secrets

async def test_settings_page_lists_fields(client):
    r = await client.get("/settings")
    assert r.status_code == 200
    for key in ("jobs.concurrency", "ui.theme", "runware.transport"):
        assert f'name="{key}"' in r.text

async def test_save_settings(client):
    r = await client.post("/settings", data={"jobs.concurrency": "4", "ui.theme": "light",
                                              "runware.transport": "rest"})
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
    fake.script["account_management"] = [[{"balance": {"amount": 7.25, "currency": "USD", "freeBalance": 0}}]]
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
