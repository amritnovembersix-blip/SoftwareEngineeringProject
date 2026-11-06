"""Class summary: weekly distribution, common gaps, reteach groups and peer pairing. Pure: no I/O."""
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from agentic.analytics.models import EvidencePoint, LinkRow
from agentic.analytics.status import AnalyticsSettings, ConceptStatus, concept_status

CHICAGO = ZoneInfo("America/Chicago")
Timeline = dict[tuple[str, str], list[EvidencePoint]]


def week_start(due_at: datetime) -> date:
    """The Monday of the due date's America/Chicago week."""
    if due_at.tzinfo is None:
        raise ValueError("due_at must be timezone-aware")
    d = due_at.astimezone(CHICAGO).date()
    return d - timedelta(days=d.weekday())


def _week_end(monday: date) -> datetime:
    """The following Monday 00:00 in Chicago (exclusive)."""
    return datetime.combine(monday + timedelta(days=7), time.min, tzinfo=CHICAGO)


@dataclass(frozen=True)
class ClassWeek:
    classroom_id: str
    concept_code: str
    week_start: date
    counts: tuple[int, int, int, int]  # students at current level 0..3
    too_little: int


def weekly_distribution(timeline: Timeline, s: AnalyticsSettings,
                        classroom_id: str) -> list[ClassWeek]:
    by_concept: dict[str, dict[str, list[EvidencePoint]]] = defaultdict(dict)
    for (student, concept), pts in timeline.items():
        mine = sorted((p for p in pts if p.classroom_id == classroom_id),
                      key=lambda p: (p.due_at, p.submission_id))
        if mine:
            by_concept[concept][student] = mine
    if not by_concept:
        return []
    firsts = {c: min(week_start(ps[0].due_at) for ps in studs.values())
              for c, studs in by_concept.items()}
    last = max(week_start(ps[-1].due_at) for studs in by_concept.values() for ps in studs.values())
    out: list[ClassWeek] = []
    for concept in sorted(by_concept):
        monday = firsts[concept]
        while monday <= last:
            end = _week_end(monday)
            counts = [0, 0, 0, 0]
            too_little = 0
            for student in sorted(by_concept[concept]):
                st = concept_status([p for p in by_concept[concept][student] if p.due_at < end], s)
                if st is None:
                    continue
                if st.status == "too_little_evidence":
                    too_little += 1
                else:
                    counts[st.current_level] += 1
            out.append(ClassWeek(classroom_id, concept, monday, tuple(counts), too_little))
            monday += timedelta(days=7)
    return out


@dataclass(frozen=True)
class Gap:
    concept_code: str
    students: tuple[str, ...]
    levels: tuple[int, ...]
    top_condition: str | None


def _attention(statuses: Iterable[ConceptStatus]) -> dict[str, list[ConceptStatus]]:
    by_concept: dict[str, list[ConceptStatus]] = defaultdict(list)
    for st in statuses:
        if st.status == "needs_attention":
            by_concept[st.concept_code].append(st)
    for lst in by_concept.values():
        lst.sort(key=lambda st: st.student_id)
    return by_concept


def _top_condition(concept: str, students: list[str], timeline: Timeline) -> str | None:
    counts: Counter[str] = Counter()
    for student in students:
        growth = [p for p in timeline.get((student, concept), []) if p.level >= 1]
        if growth:
            counts.update(growth[-1].conditions)
    if not counts:
        return None
    return min(counts, key=lambda c: (-counts[c], c))


def common_gaps(statuses: Iterable[ConceptStatus], timeline: Timeline) -> list[Gap]:
    gaps = []
    for concept, sts in _attention(statuses).items():
        ids = [st.student_id for st in sts]
        gaps.append(Gap(concept, tuple(ids), tuple(st.current_level for st in sts),
                        _top_condition(concept, ids, timeline)))
    return sorted(gaps, key=lambda g: (-len(g.students), g.concept_code))


@dataclass(frozen=True)
class ReteachGroup:
    root_concept: str
    grade_concept: str
    students: tuple[str, ...]


def _grade(concept: str) -> int:
    return int(concept.split(".")[0].split("-")[0])


def reteach_groups(statuses: Iterable[ConceptStatus], links: Iterable[LinkRow],
                   s: AnalyticsSettings, timeline: Timeline) -> list[ReteachGroup]:
    by_key: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for lk in links:
        by_key[(lk.student_id, lk.grade_concept, lk.submission_id)].add(lk.root_concept)
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for concept, sts in _attention(statuses).items():
        for st in sts:
            window = timeline.get((st.student_id, concept), [])[-s.attention_window:]
            roots: set[str] = set()
            for p in window:
                roots |= by_key.get((st.student_id, concept, p.submission_id), set())
            root = min(roots, key=lambda r: (_grade(r), r)) if roots else concept
            groups[(root, concept)].append(st.student_id)
    out = [ReteachGroup(root, concept, tuple(sorted(ids)))
           for (root, concept), ids in groups.items() if len(ids) >= s.group_min_size]
    return sorted(out, key=lambda g: (-len(g.students), g.root_concept, g.grade_concept))


@dataclass(frozen=True)
class PairSuggestion:
    concept_code: str
    tutor_id: str
    tutee_id: str


def suggest_pairs(statuses: Iterable[ConceptStatus], s: AnalyticsSettings) -> list[PairSuggestion]:
    tutors: dict[str, list[str]] = defaultdict(list)
    tutees: dict[str, list[str]] = defaultdict(list)
    for st in statuses:
        if st.status == "secure":
            tutors[st.concept_code].append(st.student_id)
        elif st.status == "needs_attention":
            tutees[st.concept_code].append(st.student_id)
    out: list[PairSuggestion] = []
    for concept in sorted(tutees):
        load: Counter[str] = Counter()
        pool = sorted(tutors.get(concept, []))
        for tutee in sorted(tutees[concept]):
            for tutor in pool:
                if tutor != tutee and load[tutor] < s.max_tutees:
                    load[tutor] += 1
                    out.append(PairSuggestion(concept, tutor, tutee))
                    break
    return out
