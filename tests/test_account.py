import pytest
from runware import RunwareError
from runwarestudio import db
from runwarestudio.services import account, migrate
from tests.fakes.fake_runware import FakeRunware, fake_factory

@pytest.fixture
def factory(tmp_path):
    p = tmp_path / "s.db"; migrate.upgrade(p)
    return db.make_session_factory(db.make_engine(p))

async def test_refresh_balance_stores_meta(factory):
    fake = FakeRunware({"account_management": [[{"balance": {"amount": 12.5, "freeBalance": 0.0, "currency": "USD"}}]]})
    info = await account.refresh_balance(fake_factory(fake), "key", "rest", factory)
    assert info.amount == 12.5 and info.currency == "USD"
    assert fake.calls[-1] == ("account_management", {"operation": "getDetails"})
    with db.session_scope(factory) as s:
        cached = account.cached_balance(s)
    assert cached is not None and cached.amount == 12.5

async def test_refresh_balance_raises_user_facing(factory):
    fake = FakeRunware({"account_management": [RunwareError("invalidApiKey", "bad")]})
    with pytest.raises(account.BalanceError) as ei:
        await account.refresh_balance(fake_factory(fake), "key", "rest", factory)
    assert ei.value.error.code == "auth"

def test_cached_balance_none_when_unset(factory):
    with db.session_scope(factory) as s:
        assert account.cached_balance(s) is None
