import pytest

from app.services.rate_limiter import RateLimiter
from tests.unit.support import FakeClock


def make(limit=3, window=60.0, max_keys=1000):
    clock = FakeClock()
    return RateLimiter(limit, window, max_keys=max_keys, clock=clock), clock


def test_allows_up_to_the_limit_then_blocks_with_retry_after():
    limiter, clock = make(limit=3, window=60)
    for _ in range(3):
        assert limiter.check("a").allowed
        clock.now += 1
    blocked = limiter.check("a")
    assert not blocked.allowed
    assert blocked.retry_after == 57  # первое событие в момент 0 выйдет из окна на 60-й секунде


def test_window_slides_and_frees_capacity():
    limiter, clock = make(limit=2, window=10)
    assert limiter.check("a").allowed
    clock.now = 5
    assert limiter.check("a").allowed
    assert not limiter.check("a").allowed
    clock.now = 10.1
    assert limiter.check("a").allowed  # первое событие вышло из окна
    assert not limiter.check("a").allowed


def test_blocked_attempts_do_not_extend_the_block():
    limiter, clock = make(limit=1, window=10)
    assert limiter.check("a").allowed
    for _ in range(50):
        assert not limiter.check("a").allowed
        clock.now += 0.1
    clock.now = 10.0
    assert limiter.check("a").allowed


def test_keys_are_independent():
    limiter, _ = make(limit=1)
    assert limiter.check("a").allowed
    assert limiter.check("b").allowed
    assert not limiter.check("a").allowed


def test_retry_after_is_at_least_one_second():
    limiter, clock = make(limit=1, window=10)
    limiter.check("a")
    clock.now = 9.99
    assert limiter.check("a").retry_after == 1


def test_memory_is_bounded_when_many_keys_arrive():
    limiter, _ = make(limit=1, max_keys=100)
    for number in range(10_000):
        limiter.check(f"client-{number}")
    assert limiter.tracked_keys() <= 100


def test_expired_keys_are_forgotten():
    limiter, clock = make(limit=1, window=10, max_keys=1000)
    for number in range(50):
        limiter.check(f"client-{number}")
    clock.now = 100
    limiter.check("fresh")
    assert limiter.tracked_keys() == 1


def test_evicting_old_keys_never_blocks_a_new_client():
    limiter, _ = make(limit=1, max_keys=2)
    for key in ("a", "b", "c", "d"):
        assert limiter.check(key).allowed


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit_is_rejected(limit):
    with pytest.raises(ValueError):
        RateLimiter(limit, 60.0)


@pytest.mark.parametrize("window", [0, -5.0])
def test_invalid_window_is_rejected(window):
    with pytest.raises(ValueError):
        RateLimiter(1, window)
