"""Graph state. Essay text (body and paragraphs) lives here only for the duration of a run."""
import operator
from typing import Annotated, TypedDict

from agentic.lms.base import Assignment
from agentic.models import (
    Dropped, Finding, MetStandard, Observation, StandardRef, SubmissionRef, Verification,
)


def merge_dicts(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


class ReviewState(TypedDict, total=False):
    submission_ref: SubmissionRef
    course_grade: int
    body: str
    assignment: Assignment | None  # context only (title + teacher's prompt)
    paragraphs: list[str]
    strategy: str
    standards: list[StandardRef]
    observations: list[Observation]
    findings: list[Finding]
    met_standards: list[MetStandard]
    met_codes: list[str] | None
    not_evident: list[str]
    verification: Verification
    llm_calls: int
    trace: dict
    dropped: Annotated[list[Dropped], operator.add]
    timings: Annotated[dict[str, float], merge_dicts]
