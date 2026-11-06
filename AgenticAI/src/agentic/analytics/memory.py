"""In-memory Adapters for tests and the lab (no database). The lab builds an InMemoryResultsReader from
its answer-key data."""
from dataclasses import dataclass, field
from datetime import datetime

from agentic.analytics.base import AnalysisRun
from agentic.analytics.models import LinkRow, VerdictRow
from agentic.analytics.status import ConceptStatus
from agentic.analytics.summary import ClassWeek, PairSuggestion, ReteachGroup


class InMemoryResultsReader:
    """`rows` are verdict rows (every classroom, every date); `links` are root-cause links, matched to a
    classroom and date through the submission ids of `rows`. `classrooms` are the ids that exist; by
    default, the classrooms of `rows`. Filters mirror the Postgres Adapter: `due_at <= as_of`.
    `courses` maps LMS course id -> classroom id and `users` LMS user id -> student id; an id not listed
    maps to itself when the classroom exists (courses) or always (users)."""

    def __init__(self, rows: list[VerdictRow], links: list[LinkRow] = (),
                 classrooms: set[str] | None = None, courses: dict[str, str] | None = None,
                 users: dict[str, str] | None = None):
        self._courses, self._users = dict(courses or {}), dict(users or {})
        self._rows = list(rows)
        self._links = list(links)
        self._classrooms = set(classrooms) if classrooms is not None else {r.classroom_id for r in rows}

    def verdict_rows(self, classroom_id: str, as_of: datetime,
                     student_id: str | None = None) -> list[VerdictRow]:
        return [r for r in self._rows if r.classroom_id == classroom_id and r.due_at <= as_of
                and (student_id is None or r.student_id == student_id)]

    def link_rows(self, classroom_id: str, as_of: datetime) -> list[LinkRow]:
        subs = {r.submission_id for r in self.verdict_rows(classroom_id, as_of)}
        return [lk for lk in self._links if lk.submission_id in subs]

    def latest_due(self, classroom_id: str) -> datetime | None:
        return max((r.due_at for r in self._rows if r.classroom_id == classroom_id), default=None)

    def classroom_exists(self, classroom_id: str) -> bool:
        return classroom_id in self._classrooms | set(self._courses.values())

    def classroom_for_course(self, lms_course_id: str) -> str | None:
        found = self._courses.get(lms_course_id, lms_course_id)
        return found if self.classroom_exists(found) else None

    def student_for_user(self, lms_user_id: str) -> str | None:
        return self._users.get(lms_user_id, lms_user_id)


@dataclass(frozen=True)
class SavedRun:
    run: AnalysisRun
    statuses: list[ConceptStatus]
    weeks: list[ClassWeek]
    groups: list[ReteachGroup]
    pairs: list[PairSuggestion]


@dataclass
class InMemoryAnalyticsWriter:
    started: list[AnalysisRun] = field(default_factory=list)
    saved: list[SavedRun] = field(default_factory=list)
    failed: list[tuple[AnalysisRun, str]] = field(default_factory=list)

    def start(self, run: AnalysisRun) -> None:
        self.started.append(run)

    def save(self, run: AnalysisRun, statuses, weeks, groups, pairs) -> None:
        self.saved.append(SavedRun(run, list(statuses), list(weeks), list(groups), list(pairs)))

    def fail(self, run: AnalysisRun, error: str) -> None:
        self.failed.append((run, error))
