import pytest

from agentic.llm.base import DailyQuotaExceeded
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


def _fake_time():
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    return now, sleeps, (lambda: now[0]), sleep


def test_tpm_second_call_waits_for_window():
    now, sleeps, clock, sleep = _fake_time()
    rl = RateLimiter(rpm=1000, tpm=1000, clock=clock, sleep=sleep)
    rl.acquire(600)
    rl.acquire(600)
    assert sum(sleeps) == pytest.approx(60.0)


def test_tpm_estimate_over_budget_waits_for_empty_window_then_proceeds():
    now, sleeps, clock, sleep = _fake_time()
    rl = RateLimiter(rpm=1000, tpm=1000, clock=clock, sleep=sleep)
    rl.acquire(5000)  # window empty: goes straight through
    assert sum(sleeps) < 1
    rl.acquire(5000)  # must wait for the first to leave the window, then proceed
    assert sum(sleeps) == pytest.approx(60.0)


def test_record_corrects_window():
    now, sleeps, clock, sleep = _fake_time()
    rl = RateLimiter(rpm=1000, tpm=1000, clock=clock, sleep=sleep)
    rl.acquire(900)
    rl.record(100, 900)  # the call was far smaller than estimated
    rl.acquire(800)
    assert sum(sleeps) < 1  # only the rpm spacing, no token wait


def test_rpd_raises_before_waiting_and_resets_next_day():
    day = [1]
    now, sleeps, clock, sleep = _fake_time()
    rl = RateLimiter(rpm=1000, rpd=2, clock=clock, sleep=sleep, today=lambda: day[0])
    rl.acquire()
    rl.acquire()
    with pytest.raises(DailyQuotaExceeded, match="daily request budget reached"):
        rl.acquire()
    assert sum(sleeps) < 1
    day[0] = 2
    rl.acquire()


def test_retry_after_hint_overrides_backoff():
    sleeps: list[float] = []
    n = {"c": 0}

    def fn():
        n["c"] += 1
        if n["c"] == 1:
            raise Retryable()
        return "ok"

    assert call_with_retries(fn, lambda e: True, sleep=sleeps.append, retry_after=lambda e: 7.0) == "ok"
    assert sleeps == [7.0]


@pytest.mark.parametrize("hint,slept", [(-3.0, 0.0), (1e9, 60.0)])
def test_retry_after_hint_is_clamped(hint, slept):
    sleeps: list[float] = []
    n = {"c": 0}

    def fn():
        n["c"] += 1
        if n["c"] == 1:
            raise Retryable()
        return "ok"

    assert call_with_retries(fn, lambda e: True, sleep=sleeps.append, retry_after=lambda e: hint) == "ok"
    assert sleeps == [slept]
