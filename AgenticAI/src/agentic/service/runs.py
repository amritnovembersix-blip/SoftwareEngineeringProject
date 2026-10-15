"""In-memory run store for the HTTP service (contract: contracts/agentic-api.yaml `Run`).

Runs live only as long as the process. Results themselves are persisted by the ResultsWriter;
consumers read them from Postgres, keyed by (lms_submission_id, attempt).
"""
import threading
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

SkipReason = Literal["already_reviewed", "excused", "not_submitted", "unsupported_subject",
                     "no_standards_in_scope"]
Trigger = Literal["on_grade", "on_due_date", "scheduled", "manual", "after_review"]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Skip(BaseModel):
    submission_id: str
    attempt: int | None = None
    reason: SkipReason


class Run(BaseModel):
    run_id: str
    run_type: Literal["submission_review", "trend_analysis"] = "submission_review"
    trigger: Trigger
    status: Literal["queued", "running", "succeeded", "failed"] = "queued"
    submission_count: int = 0
    completed_count: int = 0
    skipped_count: int = 0
    skips: list[Skip] = []
    error: str | None = None  # exception class name only, never a message (may quote essay text)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunStore:
    """Thread-safe. Each (submission_id, attempt) has at most one live run: a queued, running or
    succeeded run is returned as a duplicate; a failed one may be retried with a new run."""

    def __init__(self):
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}
        self._by_attempt: dict[tuple[str, int], str] = {}
        self._by_scope: dict[tuple, str] = {}

    def claim(self, submission_id: str, attempt: int, trigger: Trigger) -> tuple[Run, bool]:
        key = (submission_id, attempt)
        with self._lock:
            existing = self._runs.get(self._by_attempt.get(key, ""))
            if existing and existing.status != "failed":
                return existing.model_copy(), True
            run = Run(run_id=str(uuid.uuid4()), trigger=trigger, submission_count=1, created_at=now())
            self._runs[run.run_id] = run
            self._by_attempt[key] = run.run_id
            return run.model_copy(), False

    def create(self, trigger: Trigger, submission_count: int, scope: tuple = ()) -> tuple[Run, bool]:
        """A sweep run for `scope` (e.g. (course_id, assignment_id)). While a run for the same scope is
        queued or running it is returned instead, with duplicate=True (a double click starts nothing)."""
        with self._lock:
            existing = self._runs.get(self._by_scope.get(scope, ""))
            if existing and existing.status in ("queued", "running"):
                return existing.model_copy(), True
            run = Run(run_id=str(uuid.uuid4()), trigger=trigger, submission_count=submission_count,
                      created_at=now())
            self._runs[run.run_id] = run
            self._by_scope[scope] = run.run_id
            return run.model_copy(), False

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            run = self._runs.get(run_id)
            return run.model_copy() if run else None

    def update(self, run_id: str, **fields) -> None:
        with self._lock:
            self._runs[run_id] = self._runs[run_id].model_copy(update=fields)
