from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from .. import db
from ..models import utcnow
from ..runware.errors import UserFacingError, classify
from . import meta


@dataclass(frozen=True)
class BalanceInfo:
    amount: float
    currency: str
    free: float
    fetched_at: datetime


class BalanceError(Exception):
    def __init__(self, error: UserFacingError):
        super().__init__(error.message)
        self.error = error


async def refresh_balance(
    client_factory, api_key: str, transport: str, session_factory: sessionmaker[Session]
) -> BalanceInfo:
    try:
        async with client_factory(api_key, transport) as client:
            rows = await client.account_management({"operation": "getDetails"})
    except Exception as e:  # noqa: BLE001
        raise BalanceError(classify(e)) from e
    bal = (rows[0] if rows else {}).get("balance") or {}
    info = BalanceInfo(
        float(bal.get("amount", 0.0)),
        str(bal.get("currency", "USD")),
        float(bal.get("freeBalance", 0.0)),
        utcnow(),
    )
    with db.session_scope(session_factory) as s:
        meta.set(s, "account.balance", repr(info.amount))
        meta.set(s, "account.free", repr(info.free))
        meta.set(s, "account.currency", info.currency)
        meta.set(s, "account.balance_at", info.fetched_at.isoformat())
    return info


def cached_balance(session: Session) -> BalanceInfo | None:
    amount, at = meta.get(session, "account.balance"), meta.get(session, "account.balance_at")
    if amount is None or at is None:
        return None
    return BalanceInfo(
        float(amount),
        meta.get(session, "account.currency") or "USD",
        float(meta.get(session, "account.free") or 0.0),
        datetime.fromisoformat(at),
    )
