from runware import RunwareError

from vjhstudio.runware import errors


def test_known_code_maps_to_message():
    e = errors.classify(RunwareError("invalidApiKey", "Invalid API key"))
    assert e.code == "auth" and "Settings" in e.message and e.retryable is False


def test_rate_limit_is_retryable():
    e = errors.classify(RunwareError("rateLimitExceeded", "slow down"))
    assert e.code == "rateLimit" and e.retryable is True


def test_unknown_exception():
    e = errors.classify(ValueError("boom"))
    assert e.code == "unknown" and "boom" in e.message
