"""Plain records for analytics. They hold ids, codes, levels, booleans, confidences, dates and the
finding condition only; no student text is ever held here."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class VerdictRow:
    student_id: str
    classroom_id: str
    submission_id: str
    due_at: datetime
    submitted_at: datetime
    submission_type: str
    standard_code: str
    verdict: str  # meets | growth_area | not_evident
    support_level: str | None
    notable_strength: bool
    confidence: float | None
    finding_condition: str | None
    overruled: bool  # the finding has a teacher response of disagree or dismiss
    implied: bool = False  # a meets derived from a higher met grade: nothing was observed at this grade


@dataclass(frozen=True)
class EvidencePoint:
    student_id: str
    classroom_id: str
    concept_code: str
    submission_id: str
    due_at: datetime
    level: int  # 0 meets, 1 approaching, 2 developing, 3 beginning
    strength: bool
    confidence: float | None
    submission_type: str
    conditions: tuple[str, ...]


@dataclass(frozen=True)
class LinkRow:
    """A finding_link joined to its finding standards: which lower-grade concept a grade concept's
    finding traces back to."""
    student_id: str
    submission_id: str
    grade_concept: str
    root_concept: str
