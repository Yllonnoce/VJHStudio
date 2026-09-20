from __future__ import annotations
from dataclasses import dataclass
from runware import RunwareError

_MESSAGES: dict[str, tuple[str, bool]] = {
    "validation": ("The model rejected a parameter: {detail}", False),
    "auth": ("RunWare rejected the API key. Check it in Settings.", False),
    "quota": ("RunWare account balance or quota exhausted. Top up at my.runware.ai.", False),
    "rateLimit": ("Rate limited by RunWare. Retrying.", True),
    "safety": ("Blocked by the content safety filter. Adjust the prompt.", False),
    "provider": ("The model provider failed: {detail}", False),
    "timeout": ("Timed out waiting for RunWare.", False),
    "notFound": ("Model or media not found: {detail}", False),
    "serverError": ("RunWare server error. Retrying once.", True),
    "connection": ("Could not reach RunWare. Check your connection.", True),
    "aborted": ("Cancelled.", False),
    "unknown": ("Unexpected error: {detail}", False),
}


@dataclass(frozen=True)
class UserFacingError:
    code: str
    message: str
    retryable: bool
    parameter: str | None = None


def classify(exc: BaseException) -> UserFacingError:
    if isinstance(exc, RunwareError):
        code = exc.code if exc.code in _MESSAGES else "unknown"
        tmpl, retry = _MESSAGES[code]
        return UserFacingError(code, tmpl.format(detail=exc.message), retry,
                               getattr(exc, "parameter", None))
    tmpl, retry = _MESSAGES["unknown"]
    return UserFacingError("unknown", tmpl.format(detail=str(exc) or exc.__class__.__name__), retry)
