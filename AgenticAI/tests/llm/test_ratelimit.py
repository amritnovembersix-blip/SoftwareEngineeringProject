import pytest

from agentic.llm.ratelimit import RateLimiter, call_with_retries


class Retryable(Exception):
    pass


def test_spacing():
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    rl = RateLimiter(rpm=10, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        rl.acquire()
    assert sum(sleeps) == pytest.approx(12.0)


def test_retries_then_succeeds():
    sleeps: list[float] = []
    n = {"c": 0}

    def fn():
        n["c"] += 1
        if n["c"] < 3:
            raise Retryable()
        return "ok"

    assert call_with_retries(fn, lambda e: True, sleep=sleeps.append) == "ok"
    assert sleeps == [2.0, 4.0]


def test_non_retryable_raises_immediately():
    sleeps: list[float] = []
    n = {"c": 0}

    def fn():
        n["c"] += 1
        raise ValueError("x")

    with pytest.raises(ValueError):
        call_with_retries(fn, lambda e: False, sleep=sleeps.append)
    assert n["c"] == 1 and sleeps == []


def test_gives_up_after_three_attempts():
    n = {"c": 0}

    def fn():
        n["c"] += 1
        raise Retryable()

    with pytest.raises(Retryable):
        call_with_retries(fn, lambda e: True, sleep=lambda s: None)
    assert n["c"] == 3


def test_concurrent_callers_get_distinct_slots():
    """Threads calling acquire() at the same moment are spaced out, never let through together."""
    import threading

    sleeps: list[float] = []
    lock = threading.Lock()

    def sleep(s):
        with lock:
            sleeps.append(s)

    rl = RateLimiter(rpm=60, clock=lambda: 0.0, sleep=sleep)  # 1 s apart, frozen clock
    threads = [threading.Thread(target=rl.acquire) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(sleeps) == pytest.approx([1.0, 2.0, 3.0, 4.0])  # first caller doesn't wait
