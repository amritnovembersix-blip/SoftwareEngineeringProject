"""v2 graph state. Submission text (body, answers, segments) lives here only for the duration of a run."""
import operator
from typing import Annotated, Literal, TypedDict

from agentic.lms.base import Assignment, Submission
from agentic.models import (
    Dropped, Finding, FindingLink, MetStandard, Observation, StandardRef, SubmissionRef, Verdict,
    Verification,
)
from agentic.review.state import merge_dicts
from agentic.review.v2.schemas import RootDetail


class ReviewStateV2(TypedDict, total=False):
    submission_ref: SubmissionRef
    course_grade: int
    assignment: Assignment
    submission_type: str
    submission: Submission
    segments: list[str]
    segment_level: Literal["paragraph", "sentence", "answer"]
    bands: list  # BandReviewer per planned reviewer, built for this course grade
    band: object  # the one BandReviewer a `band` task runs (its Send payload)
    band_results: Annotated[list[tuple], operator.add]  # (BandResult, seconds)
    standards: list[StandardRef]
    verdicts: list[Verdict]
    observations: list[Observation]
    findings: list[Finding]
    met_standards: list[MetStandard]
    not_evident: list[str]
    links: list[FindingLink]
    details: dict[str, RootDetail]
    usage: dict
    models: dict[str, str]  # model id actually used, per reviewer id (+ root_cause, verify)
    prompt_versions: dict[str, str]
    verification: Verification
    llm_calls: int
    trace: dict
    dropped: Annotated[list[Dropped], operator.add]
    timings: Annotated[dict[str, float], merge_dicts]
