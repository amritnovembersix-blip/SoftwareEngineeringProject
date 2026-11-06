"""Orchestrator: read the verdicts, compute, and save one analysis run. Pure apart from the Targets."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from agentic.analytics.base import AnalysisRun, AnalyticsWriter, ResultsReader
from agentic.analytics.status import AnalyticsSettings, ConceptStatus, concept_status
from agentic.analytics.summary import (Gap, PairSuggestion, ReteachGroup, common_gaps, reteach_groups,
                                       suggest_pairs, weekly_distribution)
from agentic.analytics.timeline import build_timeline


class UnknownClassroom(ValueError):
    pass


def everything_reviewed(reader: ResultsReader, classroom_id: str, now: datetime | None = None) -> datetime:
    """The `as_of` that means "everything reviewed so far": a reviewed submission counts even when its due
    date hasn't come yet (early work; a simulated term dated ahead of today)."""
    now = now or datetime.now(timezone.utc)
    latest = reader.latest_due(classroom_id)
    return max(now, latest) if latest else now


@dataclass(frozen=True)
class AnalysisSummary:
    statuses: list[ConceptStatus]
    groups: list[ReteachGroup]
    pairs: list[PairSuggestion]
    gaps: list[Gap]
    run_id: str


def _run(reader: ResultsReader, writer: AnalyticsWriter, classroom_id: str, student_id: str | None,
         as_of: datetime, settings: AnalyticsSettings, trigger: str,
         run_id: UUID | None = None) -> AnalysisSummary:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if not reader.classroom_exists(classroom_id):
        raise UnknownClassroom(classroom_id)
    run = AnalysisRun(run_id=run_id or uuid4(), scope="student" if student_id else "classroom",
                      classroom_id=classroom_id, student_id=student_id, as_of=as_of, trigger=trigger)
    writer.start(run)
    try:
        timeline = build_timeline(reader.verdict_rows(classroom_id, as_of, student_id))
        statuses = [st for key in sorted(timeline)
                    if (st := concept_status(timeline[key], settings)) is not None]
        weeks, groups, pairs, gaps = [], [], [], []
        if student_id is None:
            weeks = weekly_distribution(timeline, settings, classroom_id)
            groups = reteach_groups(statuses, reader.link_rows(classroom_id, as_of), settings, timeline)
            pairs = suggest_pairs(statuses, settings)
            gaps = common_gaps(statuses, timeline)
        writer.save(run, statuses, weeks, groups, pairs)
    except Exception as err:
        writer.fail(run, type(err).__name__)  # the class name only: a message may echo student text
        raise
    return AnalysisSummary(statuses, groups, pairs, gaps, str(run.run_id))


def analyze_classroom(reader: ResultsReader, writer: AnalyticsWriter, classroom_id: str, as_of: datetime,
                      settings: AnalyticsSettings, trigger: str, run_id: UUID | None = None) -> AnalysisSummary:
    """`run_id`: the id of the AnalysisRun (the service issues it first, so GET /runs/{id} shares it)."""
    return _run(reader, writer, classroom_id, None, as_of, settings, trigger, run_id)


def analyze_student(reader: ResultsReader, writer: AnalyticsWriter, classroom_id: str, student_id: str,
                    as_of: datetime, settings: AnalyticsSettings, trigger: str,
                    run_id: UUID | None = None) -> AnalysisSummary:
    """One student's concept statuses only: no weeks, groups or pairs."""
    return _run(reader, writer, classroom_id, student_id, as_of, settings, trigger, run_id)
