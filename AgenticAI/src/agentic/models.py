"""Shared models passed between the review pipeline, Adapters and the UI."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


def word_count(text: str) -> int:
    """Whitespace-separated words; same rule as the DB word_count()."""
    return len(text.split())


class StandardRef(BaseModel):
    code: str
    concept: str
    description: str
    grade_min: int
    grade_max: int


class Observation(BaseModel):
    standard_code: str
    paragraph: int  # 1-based
    reason: str
    severity: str | None = None  # approaching | developing | beginning (full-list strategy)


class Finding(BaseModel):
    standard_code: str
    concept: str
    description: str
    paragraphs: list[int]
    reasons: list[str]
    observation_count: int
    summary: str
    severity: str | None = None  # the most severe of its observations
    p_correct: float | None = None


class MetStandard(BaseModel):
    standard_code: str
    concept: str
    p_met: float | None = None
    possible_miss: bool = False


class Dropped(BaseModel):
    stage: str
    reason: str


class SubmissionRef(BaseModel):
    course_id: str
    assignment_id: str
    user_id: str
    submission_id: str
    attempt: int
    submitted_at: datetime | None = None  # filled in by fetch


class Verification(BaseModel):
    status: Literal["ok", "unavailable", "disabled"]
    model: str | None = None
    input_tokens: int | None = None


class ReviewResult(BaseModel):
    submission_ref: SubmissionRef
    course_grade: int
    strategy: Literal["full", "rag"]
    standards: list[StandardRef]
    observations: list[Observation]
    findings: list[Finding]
    met_standards: list[MetStandard]
    not_evident: list[str] = Field(default_factory=list)
    dropped: list[Dropped]
    verification: Verification
    timings: dict[str, float] = Field(default_factory=dict)
    llm_calls: int = 0
    trace: dict = Field(default_factory=dict)
