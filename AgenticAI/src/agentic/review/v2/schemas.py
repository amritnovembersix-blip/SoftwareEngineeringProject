"""Reply schemas for the v2 band reviewers. Values are validated by the reviewer's guards, not here,
so one bad entry is dropped instead of failing the whole reply."""
from typing import Literal

from pydantic import BaseModel


class SegmentEvidence(BaseModel):
    segment: int  # 1-based, as cited ([S1] -> 1)
    reason: str


class StandardJudgment(BaseModel):
    standard_code: str
    verdict: str  # meets | growth_area | not_evident
    support_level: str | None = None  # required for growth_area: approaching | developing | beginning
    evidence: list[SegmentEvidence] = []
    notable_strength: bool = False
    strength_note: str | None = None


class PerStandardOut(BaseModel):
    judgments: list[StandardJudgment]


class StepLevel(BaseModel):
    grade: int  # the grade key shown on the step's lines (9 for 9-10)
    support_level: Literal["approaching", "developing", "beginning"]


class ProgressionJudgment(BaseModel):
    group: str  # strand.bigIdea.concept, e.g. W.2.A
    evident: bool = True  # False: the work gives no chance to show the skill (every step not_evident)
    working_grade: int | None = None  # highest step met; None when none is
    support_level: str | None = None  # v1 prompt: one level for every step above the working grade
    step_levels: list[StepLevel] = []  # v2 prompt: a level for each step above the working grade
    evidence: list[SegmentEvidence] = []
    notable_strength: bool = False
    strength_note: str | None = None


class ProgressionOut(BaseModel):
    progressions: list[ProgressionJudgment]


class StepJudgment(BaseModel):
    """Search mode (progression.v3): one group's verdict on the one grade step it was shown."""
    group: str  # strand.bigIdea.concept, e.g. W.2.A
    verdict: str  # meets | growth_area | not_evident
    support_level: str | None = None  # required for growth_area: approaching | developing | beginning
    evidence: list[SegmentEvidence] = []
    notable_strength: bool = False
    strength_note: str | None = None


class StepOut(BaseModel):
    judgments: list[StepJudgment]


class RootLink(BaseModel):
    finding_code: str  # a grade 6+ growth area
    root_code: str  # a grade 3-5 growth area sharing a segment with it
    rationale: str


class RootDetail(BaseModel):
    standard_code: str
    condition: str | None = None  # from the concept's condition vocabulary
    pattern: str | None = None  # consistent | occasional
    skill_state: str | None = None  # missing | misapplied


class RootCauseOut(BaseModel):
    links: list[RootLink]
    details: list[RootDetail]
