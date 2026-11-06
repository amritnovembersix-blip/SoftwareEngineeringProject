"""Targets for the analytics: where verdicts are read from and where snapshots are written."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from agentic.analytics.models import LinkRow, VerdictRow
from agentic.analytics.status import ConceptStatus
from agentic.analytics.summary import ClassWeek, PairSuggestion, ReteachGroup


@dataclass(frozen=True)
class AnalysisRun:
    """One analysis run. `trigger` is the review_run trigger_type (manual, after_review, scheduled, ...)."""
    run_id: UUID
    scope: Literal["classroom", "student"]
    classroom_id: str
    student_id: str | None
    as_of: datetime
    trigger: str


@runtime_checkable
class ResultsReader(Protocol):
    def verdict_rows(self, classroom_id: str, as_of: datetime,
                     student_id: str | None = None) -> list[VerdictRow]:
        """Verdicts of the current (not superseded) succeeded reviews whose assignment was due by
        `as_of`; one student's only when `student_id` is given."""
        ...

    def link_rows(self, classroom_id: str, as_of: datetime) -> list[LinkRow]:
        """Root-cause links of those same reviews."""
        ...

    def latest_due(self, classroom_id: str) -> datetime | None:
        """The latest due date among the classroom's current succeeded reviews; None if it has none."""
        ...

    def classroom_exists(self, classroom_id: str) -> bool: ...

    def classroom_for_course(self, lms_course_id: str) -> str | None:
        """The SASS classroom id of an LMS course, or None."""
        ...

    def student_for_user(self, lms_user_id: str) -> str | None:
        """The SASS student id of an LMS user, or None."""
        ...


@runtime_checkable
class AnalyticsWriter(Protocol):
    def start(self, run: AnalysisRun) -> None:
        """Record the run as running."""
        ...

    def save(self, run: AnalysisRun, statuses: list[ConceptStatus], weeks: list[ClassWeek],
             groups: list[ReteachGroup], pairs: list[PairSuggestion]) -> None:
        """Write every snapshot row and mark the run succeeded (finished_at set), in one transaction."""
        ...

    def fail(self, run: AnalysisRun, error: str) -> None:
        """Mark the run failed. `error` is the exception class name only, never a message."""
        ...
