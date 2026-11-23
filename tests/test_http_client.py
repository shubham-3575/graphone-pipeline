import random
from src.http_client import backoff_delay, parse_retry_after


def test_backoff_bounded_and_jittered():
    for a in range(12):
        d = backoff_delay(a, base=1, cap=60)
        assert 0 <= d <= 60
    assert backoff_delay(3, 1, 60, rng=lambda: 1.0) == 8
    assert backoff_delay(20, 1, 60, rng=lambda: 1.0) == 60


def test_retry_after():
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
