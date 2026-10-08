"""Review policy (contract `ReviewPolicy`). Held in memory; the scheduler is not built yet, so
`on_due_date` and `schedules` are stored and validated but do not fire."""
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

_CRON_FIELD = re.compile(r"^[\d*/,\-]+$")


class SubmissionReviewPolicy(BaseModel):
    on_grade: bool
    on_due_date: bool
    schedules: list[str]


class TrendAnalysisPolicy(BaseModel):
    after_review: bool
    min_new_findings: int = Field(ge=1)
    schedules: list[str]


class ReviewPolicy(BaseModel):
    submission_review: SubmissionReviewPolicy
    trend_analysis: TrendAnalysisPolicy
    timezone: str


DEFAULT_POLICY = ReviewPolicy(
    submission_review=SubmissionReviewPolicy(on_grade=True, on_due_date=False, schedules=[]),
    trend_analysis=TrendAnalysisPolicy(after_review=False, min_new_findings=3, schedules=[]),
    timezone="America/Chicago",
)


def is_cron(expr: str) -> bool:
    """A 5-field cron expression (minute hour day month weekday) of digits, *, /, , and -."""
    fields = expr.split()
    return len(fields) == 5 and all(_CRON_FIELD.match(f) for f in fields)


def policy_error(p: ReviewPolicy) -> str | None:
    """`invalid_cron`, `invalid_timezone`, or None when the policy is valid."""
    if not all(is_cron(c) for c in p.submission_review.schedules + p.trend_analysis.schedules):
        return "invalid_cron"
    try:
        ZoneInfo(p.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return "invalid_timezone"
    return None
