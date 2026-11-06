"""Evidence timeline: one point per (student, concept, submission) from saved verdicts."""
from collections import defaultdict
from collections.abc import Iterable

from agentic.analytics.models import EvidencePoint, VerdictRow
from agentic.standards.codes import concept_of as concept_code

LEVELS = {"approaching": 1, "developing": 2, "beginning": 3}


def _point(rows: list[VerdictRow], concept: str) -> EvidencePoint | None:
    if all(r.verdict == "not_evident" for r in rows):
        return None
    # an overruled growth verdict counts as meets
    growth = [r for r in rows if r.verdict == "growth_area" and not r.overruled]
    confs = [r.confidence for r in growth if r.confidence is not None]
    first = rows[0]
    return EvidencePoint(
        student_id=first.student_id, classroom_id=first.classroom_id, concept_code=concept,
        submission_id=first.submission_id, due_at=first.due_at,
        level=max((LEVELS.get(r.support_level or "", 0) for r in growth), default=0),
        strength=any(r.notable_strength for r in rows),
        confidence=min(confs) if confs else None,
        submission_type=first.submission_type,
        conditions=tuple(sorted({r.finding_condition for r in growth if r.finding_condition})))


def build_timeline(rows: Iterable[VerdictRow]) -> dict[tuple[str, str], list[EvidencePoint]]:
    groups: dict[tuple[str, str, str], list[VerdictRow]] = defaultdict(list)
    for r in rows:
        if not r.implied:  # a met higher grade implies this one, but nothing was observed here
            groups[(r.student_id, concept_code(r.standard_code), r.submission_id)].append(r)
    keyed: dict[tuple[str, str], list[tuple[tuple, EvidencePoint]]] = defaultdict(list)
    for (student, concept, _), rs in groups.items():
        p = _point(rs, concept)
        if p is not None:
            order = (p.due_at, min(r.submitted_at for r in rs), p.submission_id)
            keyed[(student, concept)].append((order, p))
    return {k: [p for _, p in sorted(v, key=lambda x: x[0])] for k, v in keyed.items()}
