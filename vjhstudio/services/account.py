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


def parse_balance(row: dict) -> tuple[float, str, float]:
    """Read (amount, currency, free) from a getDetails row.

    The live API returns ``"balance": 37.09`` (a bare number); the docs describe
    ``"balance": {"amount": ..., "currency": ..., "freeBalance": ...}``. Accept both.
    """
    bal = row.get("balance")
    if isinstance(bal, dict):
        return (
            _num(bal.get("amount")),
            str(bal.get("currency") or "USD"),
            _num(bal.get("freeBalance")),
        )
    return _num(bal), str(row.get("currency") or "USD"), _num(row.get("freeBalance"))


def _num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


async def refresh_balance(
    client_factory, api_key: str, transport: str, session_factory: sessionmaker[Session]
) -> BalanceInfo:
    try:
        async with client_factory(api_key, transport) as client:
            rows = await client.account_management({"operation": "getDetails"})
    except Exception as e:  # noqa: BLE001
        raise BalanceError(classify(e)) from e
    amount, currency, free = parse_balance(rows[0] if rows else {})
    info = BalanceInfo(amount, currency, free, utcnow())
    with db.session_scope(session_factory) as s:
        meta.set(s, "account.balance", repr(info.amount))
        meta.set(s, "account.free", repr(info.free))
        meta.set(s, "account.currency", info.currency)
        meta.set(s, "account.balance_at", info.fetched_at.isoformat())
    return info


BALANCE_MAX_AGE_S = 30


def is_stale(info: BalanceInfo | None, max_age_s: int = BALANCE_MAX_AGE_S) -> bool:
    """True when the header chip should ask RunWare again rather than show the cache."""
    if info is None:
        return True
    return (utcnow() - info.fetched_at).total_seconds() > max_age_s


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
