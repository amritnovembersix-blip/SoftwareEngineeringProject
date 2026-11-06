"""review_one: the single entry point for one review with chosen options (CLI, service and lab).

A composition point: it builds through agentic.wiring (spec section 4a puts review_one in review/)."""
from agentic import wiring
from agentic.config import Settings
from agentic.models import ReviewResult, SubmissionRef
from agentic.review.v2.options import ReviewOptions
from agentic.settings.base import SettingsStore


def review_one(s: Settings, course_id: str, assignment_id: str, user_id: str, options: ReviewOptions,
               store: SettingsStore | None = None, conn=None) -> ReviewResult:
    """Review one student's latest attempt. Settings come from `store` when given, else the TOML
    defaults, snapshotted now. Nothing is persisted. Errors propagate (DailyQuotaExceeded included);
    callers record the exception class only."""
    lms = wiring.build_course_lms(s, course_id)
    sub = next((x for x in lms.list_submissions(course_id, assignment_id) if x.user_id == user_id), None)
    if sub is None:
        raise LookupError("no submission found")
    ref = SubmissionRef(course_id=course_id, assignment_id=assignment_id, user_id=user_id,
                        submission_id=sub.id, attempt=sub.attempt, submitted_at=sub.submitted_at)
    resolved = wiring.resolve_options(s, options, store)
    run_settings = s.model_copy(update={"review_pipeline": resolved.pipeline, "verify": resolved.verify})
    reviewer = wiring.build_course_reviewer(run_settings, course_id, conn=conn, options=resolved)
    return reviewer.run(ref)
