"""Decides when a classroom's trend analysis runs: at most one at a time, collapsing bursts of requests.

Generic over `run_fn(classroom_id, trigger)`: the app's run_fn makes the RunStore entry, snapshots the
settings and runs the analysis. Nothing here knows about analytics, so it is tested with a fake run_fn
and fake timers.

- `request`: run now (off the calling thread), or, if that classroom's run is in flight, set one rerun
  flag. Many requests during a run still give one follow-up run, which sees everything saved so far.
- `debounce`: (re)start a `delay_s` timer that then calls `request("after_review")`. A burst of reviews
  (webhooks) gives one analysis, `delay_s` after the last one.
- `exclusive`: a per-classroom lock. A caller that runs an analysis itself (the POST) holds it so that
  an automatic run of the same classroom waits instead of overlapping.
"""
import threading
from collections.abc import Callable
from contextlib import contextmanager

AFTER_REVIEW = "after_review"


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="analysis", daemon=True).start()


class AnalysisScheduler:
    def __init__(self, run_fn: Callable[[str, str], None], delay_s: float = 60.0,
                 timer_factory: Callable = threading.Timer, spawn: Callable[[Callable[[], None]], None] = _thread):
        self._run_fn, self._delay_s, self._timer_factory, self._spawn = run_fn, delay_s, timer_factory, spawn
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._rerun: dict[str, str] = {}  # classroom -> trigger of the single follow-up
        self._timers: dict[str, object] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._idle = threading.Condition(self._lock)

    def request(self, classroom_id: str, trigger: str) -> None:
        with self._lock:
            if classroom_id in self._running:
                self._rerun[classroom_id] = trigger
                return
            self._running.add(classroom_id)
        self._spawn(lambda: self._loop(classroom_id, trigger))

    def debounce(self, classroom_id: str) -> None:
        with self._lock:
            old = self._timers.pop(classroom_id, None)
            if old is not None:
                old.cancel()
            timer = self._timer_factory(self._delay_s, self._fire, (classroom_id,))
            timer.daemon = True
            self._timers[classroom_id] = timer
        timer.start()

    def _fire(self, classroom_id: str) -> None:
        with self._lock:
            self._timers.pop(classroom_id, None)
        self.request(classroom_id, AFTER_REVIEW)

    @contextmanager
    def exclusive(self, classroom_id: str):
        with self._lock:
            lock = self._locks.setdefault(classroom_id, threading.Lock())
        with lock:
            yield

    def _loop(self, classroom_id: str, trigger: str) -> None:
        while True:
            try:
                with self.exclusive(classroom_id):
                    self._run_fn(classroom_id, trigger)
            except Exception:  # noqa: BLE001 - run_fn records its own failure; a message may echo student text
                pass
            with self._lock:
                nxt = self._rerun.pop(classroom_id, None)
                if nxt is None:
                    self._running.discard(classroom_id)
                    self._idle.notify_all()
                    return
            trigger = nxt

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until no classroom is running (tests)."""
        with self._idle:
            return self._idle.wait_for(lambda: not self._running, timeout)
