"""Shared models passed between the review pipeline, Adapters and the UI."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agentic.llm.base import Usage


def word_count(text: str) -> int:
    """Whitespace-separated words; same rule as the DB word_count()."""
    return len(text.split())


class StandardRef(BaseModel):
    code: str
    concept: str
    description: str
    grade_min: int
    grade_max: int
    strand: str = ""
    sub_expectation: str | None = None


class Observation(BaseModel):
    standard_code: str
    paragraph: int  # 1-based
    reason: str
    severity: str | None = None  # approaching | developing | beginning (full-list strategy)
    level: Literal["sentence", "paragraph", "item", "answer"] = "paragraph"  # v2: what `paragraph` indexes


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
    # v2 fields
    support_level: Literal["approaching", "developing", "beginning"] | None = None
    confidence: float | None = None
    condition: str | None = None
    pattern: Literal["consistent", "occasional"] | None = None
    skill_state: Literal["missing", "misapplied"] | None = None


class Verdict(BaseModel):
    """v2: one reviewer's call on one standard."""
    standard_code: str
    verdict: Literal["meets", "growth_area", "not_evident"]
    support_level: Literal["approaching", "developing", "beginning"] | None = None
    working_grade: int | None = None
    notable_strength: bool = False
    strength_note: str | None = None  # 1-40 words, if and only if notable_strength
    segments: list[int] = Field(default_factory=list)
    confidence: float | None = None
    reviewer: str

    @model_validator(mode="after")
    def _check(self):
        if (self.verdict == "growth_area") != (self.support_level is not None):
            raise ValueError("support_level is required if and only if the verdict is growth_area")
        # mirrors V7 verdict_strength_note_iff_notable
        if self.notable_strength != (self.strength_note is not None):
            raise ValueError("notable_strength and strength_note go together")
        if self.strength_note is not None and not 1 <= word_count(self.strength_note) <= 40:
            raise ValueError("strength_note must be 1-40 words")
        return self


class FindingLink(BaseModel):
    """v2: a grade-level finding traced to a lower-grade root standard."""
    finding_code: str
    root_code: str
    rationale: str  # 1-40 words

    @model_validator(mode="after")
    def _check(self):
        if not 1 <= word_count(self.rationale) <= 40:
            raise ValueError("rationale must be 1-40 words")
        return self


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
    # v2 fields
    pipeline: Literal["mvp", "v2"] = "mvp"
    submission_type: str = "essay"
    verdicts: list[Verdict] = Field(default_factory=list)
    links: list[FindingLink] = Field(default_factory=list)
    usage: dict[str, Usage] = Field(default_factory=dict)  # per reviewer id
    # model id actually used, per reviewer id plus "root_cause" and "verify" when they ran
    models: dict[str, str] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    settings_hash: str | None = None
    tuning_hash: str | None = None
