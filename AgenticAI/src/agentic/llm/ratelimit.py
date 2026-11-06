"""Pacing and retry helpers shared by the LLM Adapters."""
import datetime
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from agentic.llm.base import DailyQuotaExceeded

T = TypeVar("T")
MAX_RETRY_AFTER = 60.0  # seconds; a longer (or negative) Retry-After hint is clamped


class RateLimiter:
    """Spaces calls at least 60/rpm seconds apart; optionally holds tokens per minute (`tpm`, a sliding
    60-second window) and requests per day (`rpd`, per local date). Thread-safe: concurrent callers each
    reserve their own slot, then wait outside the lock."""

    def __init__(self, rpm: int, tpm: int | None = None, rpd: int | None = None,
                 clock=time.monotonic, sleep=time.sleep, today=datetime.date.today):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self._interval = 60.0 / rpm
        self._tpm = tpm
        self._rpd = rpd
        self._clock = clock
        self._sleep = sleep
        self._today = today
        self._next_ok: float | None = None
        self._window: list[tuple[float, int]] = []  # (time, tokens), oldest first
        self._day = None
        self._day_calls = 0
        self._lock = threading.Lock()

    def acquire(self, estimated_tokens: int = 0) -> None:
        with self._lock:
            if self._rpd is not None:
                today = self._today()
                if today != self._day:
                    self._day, self._day_calls = today, 0
                if self._day_calls >= self._rpd:
                    raise DailyQuotaExceeded()
                self._day_calls += 1
            now = self._clock()
            start = now if self._next_ok is None or now >= self._next_ok else self._next_ok
            if self._tpm is not None:
                start = self._token_start(start, estimated_tokens)
                self._window.append((start, estimated_tokens))
            self._next_ok = start + self._interval
        if start > now:
            self._sleep(start - now)

    def _token_start(self, start: float, estimated: int) -> float:
        """Earliest time >= start at which `estimated` more tokens fit in the 60 s window (or the window
        is empty, for an estimate bigger than the whole budget)."""
        self._window = [(t, n) for t, n in self._window if t > start - 60.0]
        pending = list(self._window)
        used = sum(n for _, n in pending)
        while pending and used + estimated > self._tpm:
            t, n = pending.pop(0)
            used -= n
            start = max(start, t + 60.0)
        return start

    def record(self, actual_tokens: int, estimated_tokens: int) -> None:
        """Replace a call's estimate with its real token count (a correction entry in the window)."""
        if self._tpm is None or actual_tokens == estimated_tokens:
            return
        with self._lock:
            self._window.append((self._clock(), actual_tokens - estimated_tokens))


def call_with_retries(
    fn: Callable[[], T],
    is_retryable: Callable[[Exception], bool],
    attempts: int = 3,
    base_delay: float = 2.0,
    sleep=time.sleep,
    retry_after: Callable[[Exception], float | None] | None = None,
) -> T:
    """Call fn; retry retryable errors with exponential backoff (2 s, 4 s, ...), or the server's
    Retry-After hint when `retry_after` finds one on the error (clamped to 0..MAX_RETRY_AFTER)."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts - 1 or not is_retryable(exc):
                raise
            hint = retry_after(exc) if retry_after else None
            sleep(min(max(hint, 0.0), MAX_RETRY_AFTER) if hint is not None else base_delay * 2**attempt)
    raise AssertionError("unreachable")
