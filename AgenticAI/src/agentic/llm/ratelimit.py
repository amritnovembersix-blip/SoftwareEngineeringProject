"""Pacing and retry helpers shared by the LLM Adapters."""
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RateLimiter:
    """Spaces calls at least 60/rpm seconds apart."""

    def __init__(self, rpm: int, clock=time.monotonic, sleep=time.sleep):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self._interval = 60.0 / rpm
        self._clock = clock
        self._sleep = sleep
        self._next_ok: float | None = None

    def acquire(self) -> None:
        now = self._clock()
        if self._next_ok is not None and now < self._next_ok:
            self._sleep(self._next_ok - now)
            now = self._next_ok
        self._next_ok = now + self._interval


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
