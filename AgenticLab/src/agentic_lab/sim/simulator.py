"""The semester simulator: moves a clock week by week, releasing each due assignment and sending its events."""
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentic.coursedata import CourseDir

from agentic_lab.sim.release import Releaser
from agentic_lab.sim.schedule import ScheduledAssignment, due_in_week, schedule
from agentic_lab.sim.settings import REPO_ROOT
from agentic_lab.sim.state import SentEvent, SimState, load_state, save_state
from agentic_lab.sim.trigger import GradedEvent, ReviewTrigger, RunsClient, TriggerError

POLL_SECONDS = 2
WAIT_LIMIT_SECONDS = 30 * 60
_DONE = {"succeeded", "failed"}
_RUN_STATES = ("queued", "running", "succeeded", "failed")

Progress = Callable[[int, ScheduledAssignment, int, int], None]
WeekDone = Callable[[int, int, int], None]  # (week, failed runs, unknown runs), after a waited week


@dataclass(frozen=True)
class AdvanceReport:
    week: int  # the week the clock ended on
    released: int  # submission rows released by this call
    sent: int  # new events sent by this call
    already_sent: int  # rows skipped because their event was already in the state
    failed: int = 0  # with wait: runs that finished failed
    unknown: int = 0  # with wait: runs the service no longer knows (for example after a restart)


@dataclass(frozen=True)
class AssignmentStatus:
    version: str
    file_id: str
    week: int
    released: bool
    sent: int
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    unknown: int = 0


class Simulator:
    """Replays the courses in `versions` through a Releaser and a ReviewTrigger (Targets), keeping its clock
    and the sent events in sim_dir/state.json so a rerun resumes without sending anything twice."""

    def __init__(self, data_dir: Path, versions: list[str], releaser: Releaser, trigger: ReviewTrigger,
                 runs: RunsClient | None, sim_dir: Path, concurrency: int = 4,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._data = Path(data_dir)
        self._versions = list(versions)
        self._releaser, self._trigger, self._runs = releaser, trigger, runs
        self._sim = Path(sim_dir)
        self._concurrency = concurrency
        self._sleep, self._clock = sleep, clock
        self._schedules = {v: schedule(self._data, v) for v in self._versions}

    def _check_sim_dir(self) -> None:
        """Refuse to empty a folder that is not the simulator's own: one holding the source data or the repo,
        or a non-empty folder with no state.json. Messages name the setting, never the path."""
        sim = self._sim.resolve()
        for guarded in (self._data.resolve(), REPO_ROOT.resolve()):
            if sim == guarded or sim in guarded.parents:
                raise ValueError("SIM_DIR must not be or contain the data folder or the repository; "
                                 "point it at a folder of its own")
        if sim.is_dir() and any(sim.iterdir()) and not (sim / "state.json").is_file():
            raise ValueError("SIM_DIR is a non-empty folder without state.json; reset refuses to empty it")

    def reset(self) -> None:
        """Empty sim_dir (released files and state) and start again at week 0."""
        self._check_sim_dir()
        self._sim.mkdir(parents=True, exist_ok=True)
        for p in self._sim.iterdir():
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
        save_state(self._sim, SimState())

    def run_through(self, week: int, wait: bool = False, progress: Progress | None = None,
                    week_done: WeekDone | None = None) -> AdvanceReport:
        self.reset()
        return self.advance(weeks=week, wait=wait, progress=progress, week_done=week_done)

    def advance(self, weeks: int = 1, wait: bool = False, progress: Progress | None = None,
                week_done: WeekDone | None = None) -> AdvanceReport:
        """Process weeks week+1 .. week+weeks. A TriggerError stops the advance with the week unchanged; the
        events sent before it stay recorded, so the next advance resumes."""
        if wait and self._runs is None:
            raise ValueError("wait needs a runs client")
        state = load_state(self._sim)
        released = sent = already = failed = unknown = 0
        # Past weeks whose files were deleted by hand come back, without sending anything.
        for version in self._versions:
            for item in self._schedules[version]:
                if item.week <= state.week and not self._releaser.is_released(version, item.file_id):
                    released += self._releaser.release(version, item.file_id)
        for week in range(state.week + 1, state.week + weeks + 1):
            week_items = []
            for version in self._versions:
                for item in due_in_week(self._schedules[version], week):
                    week_items.append(item)
                    n_released = 0
                    if not self._releaser.is_released(version, item.file_id):
                        n_released = self._releaser.release(version, item.file_id)
                    n_sent, n_already = self._send(state, item)
                    released, sent, already = released + n_released, sent + n_sent, already + n_already
                    if progress is not None:
                        progress(week, item, n_released, n_sent)
            if wait:
                n_failed, n_unknown = self._wait(week, state, week_items)
                failed, unknown = failed + n_failed, unknown + n_unknown
                if week_done is not None:
                    week_done(week, n_failed, n_unknown)
            state.week = week
            save_state(self._sim, state)
        return AdvanceReport(week=state.week, released=released, sent=sent, already_sent=already,
                             failed=failed, unknown=unknown)

    def _send(self, state: SimState, item: ScheduledAssignment) -> tuple[int, int]:
        """Send the events of one assignment that are not in the state yet; returns (sent, already sent)."""
        rows = CourseDir(self._data, item.version).submissions(item.file_id)
        done = {s.key for s in state.sent}
        pending = [r for r in rows if (item.course_id, r.submission_id, r.attempt) not in done]
        stop = threading.Event()
        lock = threading.Lock()
        errors: list[Exception] = []
        n_sent = 0

        def send_one(row) -> None:
            nonlocal n_sent
            if stop.is_set():
                return
            event = GradedEvent(submission_id=row.submission_id, assignment_id=item.lms_id,
                                course_id=item.course_id, user_id=row.student_hash, attempt=row.attempt,
                                event_time=item.event_time)
            try:
                run_id = self._trigger.send(event)
            except Exception as e:
                stop.set()
                with lock:
                    errors.append(e)
                return
            sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with lock:
                state.sent.append(SentEvent(course_id=item.course_id, assignment_id=item.lms_id,
                                            submission_id=row.submission_id, attempt=row.attempt,
                                            run_id=run_id, sent_at=sent_at))
                n_sent += 1

        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            list(pool.map(send_one, pending))
        save_state(self._sim, state)
        if errors:
            first = next((e for e in errors if isinstance(e, TriggerError)), errors[0])
            raise first
        return n_sent, len(rows) - len(pending)

    def _week_runs(self, state: SimState, items: list[ScheduledAssignment]) -> list[str]:
        keys = {(i.course_id, i.lms_id) for i in items}
        return [s.run_id for s in state.sent if (s.course_id, s.assignment_id) in keys]

    def _wait(self, week: int, state: SimState, items: list[ScheduledAssignment]) -> tuple[int, int]:
        """Poll every run of the week's assignments until each is finished or unknown to the service; returns
        (failed, unknown) run counts."""
        pending = set(self._week_runs(state, items))
        total = len(pending)
        failed = unknown = 0
        deadline = self._clock() + WAIT_LIMIT_SECONDS
        while True:
            for run_id in list(pending):
                record = self._runs.get(run_id)
                if record is None or record.get("status") in _DONE:
                    pending.discard(run_id)
                    failed += record is not None and record.get("status") == "failed"
                    unknown += record is None
            if not pending:
                return failed, unknown
            if self._clock() >= deadline:
                raise TimeoutError(f"week {week}: {len(pending)} of {total} runs not finished after 30 minutes")
            self._sleep(POLL_SECONDS)

    def status(self) -> list[AssignmentStatus]:
        """One row per scheduled assignment of every version, with the run states of its sent events."""
        state = load_state(self._sim)
        records: dict[str, dict | None] = {}
        if self._runs is not None and state.sent:
            run_ids = list(dict.fromkeys(s.run_id for s in state.sent))
            with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
                records = dict(zip(run_ids, pool.map(self._runs.get, run_ids)))
        out = []
        for version in self._versions:
            for item in self._schedules[version]:
                runs = [s.run_id for s in state.sent
                        if s.course_id == item.course_id and s.assignment_id == item.lms_id]
                counts = dict.fromkeys(_RUN_STATES + ("unknown",), 0)
                for run_id in runs:
                    record = records.get(run_id)
                    status = record.get("status") if record is not None else None
                    counts[status if status in _RUN_STATES else "unknown"] += 1
                out.append(AssignmentStatus(version=version, file_id=item.file_id, week=item.week,
                                            released=self._releaser.is_released(version, item.file_id),
                                            sent=len(runs), **counts))
        return out
