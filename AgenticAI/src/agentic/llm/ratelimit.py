"""Pacing and retry helpers shared by the LLM Adapters."""
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RateLimiter:
    """Spaces calls at least 60/rpm seconds apart. Thread-safe: concurrent callers each reserve their
    own slot, then wait outside the lock."""

    def __init__(self, rpm: int, clock=time.monotonic, sleep=time.sleep):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self._interval = 60.0 / rpm
        self._clock = clock
        self._sleep = sleep
        self._next_ok: float | None = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            start = now if self._next_ok is None or now >= self._next_ok else self._next_ok
            self._next_ok = start + self._interval
        if start > now:
            self._sleep(start - now)


def call_with_retries(
    fn: Callable[[], T],
    is_retryable: Callable[[Exception], bool],
    attempts: int = 3,
    base_delay: float = 2.0,
    sleep=time.sleep,
) -> T:
    """Call fn; retry retryable errors with exponential backoff (2 s, 4 s, ...)."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts - 1 or not is_retryable(exc):
                raise
            sleep(base_delay * 2**attempt)
    raise AssertionError("unreachable")
