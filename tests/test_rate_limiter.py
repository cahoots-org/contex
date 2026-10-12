from src.core import rate_limiter
from src.core.rate_limiter import RateLimiter


def test_limits_each_key_within_the_window(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(rate_limiter.time, "monotonic", lambda: clock[0])
    monkeypatch.setenv("RATE_LIMIT_TEST", "2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW", "60")
    limiter = RateLimiter()

    assert limiter.retry_after("a", "RATE_LIMIT_TEST", 0) is None
    assert limiter.retry_after("a", "RATE_LIMIT_TEST", 0) is None
    assert limiter.retry_after("a", "RATE_LIMIT_TEST", 0) == 60
    assert limiter.retry_after("b", "RATE_LIMIT_TEST", 0) is None

    clock[0] += 60
    assert limiter.retry_after("a", "RATE_LIMIT_TEST", 0) is None


def test_non_positive_limit_disables_it(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_TEST", raising=False)
    limiter = RateLimiter()
    assert all(limiter.retry_after("a", "RATE_LIMIT_TEST", 0) is None for _ in range(100))
