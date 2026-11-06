"""Lab only: fill Postgres with demo reviews built from the v2 answer key, so the Backend and Frontend have varied,
realistic rows to build against with no API keys. Everything is labelled as demo data (`answer-key-demo`).
No model runs and no submission text is read. Production never opens `answer_key/`."""
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from agentic import dataset, wiring
from agentic.analytics.run import analyze_classroom, everything_reviewed
from agentic.analytics.status import from_effective
from agentic.config import Settings
from agentic.coursedata import CourseDir, lms_assignment_id
from agentic.llm.fake import FakeEmbedder
from agentic.models import Finding, FindingLink, Observation, ReviewResult, SubmissionRef, Verdict, Verification
from agentic.results.reset import reset_reviews
from agentic.results.seed import seed_course
from agentic.settings.base import effective, load_defs
from agentic.standards import loader
from agentic.standards.codes import concept_of
from agentic.tuning import load_conditions

from agentic_lab.analytics_lab import SUPPORT, _expected, evidenced_standards
from agentic_lab.synth.families import SkillFamily, grade_key, load_families

REVIEWER = "answer-key-demo"
VERSIONS = ("2.00-control", "2.00-intervention")
_CONFIDENCE = {1: 0.62, 2: 0.74, 3: 0.86}
_LEVEL_NAME = {1: "Approaching", 2: "Developing", 3: "Beginning"}
_OBSERVATION_LEVEL = {"essay": "paragraph", "paragraph": "paragraph", "short_answer": "answer",
                      "multiple_choice": "item"}


def _pick(key: str, n: int) -> int:
    """A stable choice from a key (Python's hash() changes per process)."""
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % n


def _root_of(code: str, planted: set[str], families: dict[str, SkillFamily]) -> str | None:
    """The lowest-grade (3-5) planted standard sharing a skill family with a grade 6+ growth area."""
    if grade_key(code) < 6:
        return None
    roots = {other for fam in families.values() if code in fam.codes
             for other in fam.codes if other in planted and grade_key(other) <= 5}
    return min(roots, key=lambda c: (grade_key(c), c)) if roots else None


def demo_results(data_dir: Path, version: str, through_week: int, families: dict[str, SkillFamily],
                 conditions: dict[str, list[str]]) -> Iterator[ReviewResult]:
    """One saved-review result per submission that has an answer-key row, up to `through_week`."""
    cd = CourseDir(data_dir, version)
    course = cd.course()
    course_id = dataset.course_id(version)
    for a in cd.assignments():
        own = _expected(data_dir, version, a.id)
        if a.week > through_week or not own:
            continue
        twin = _expected(data_dir, course.twin, a.id) if course.twin else []
        evidenced = sorted(evidenced_standards(a, own, twin))
        submitted = {r.submission_id: r.submitted_at for r in cd.submissions(a.id)}
        for row in own:
            planted = {w.standard: w.level for w in row.weaknesses}
            shown = {c for c in evidenced if c in planted}
            verdicts, observations, findings, links = [], [], [], []
            for i, code in enumerate(sorted(shown)):
                level = planted[code]
                segment = i + 1  # one observation per segment, so each finding owns exactly its own evidence
                support = SUPPORT[level]
                choices = conditions.get(".".join(code.split(".")[1:4]), [])
                verdicts.append(Verdict(standard_code=code, verdict="growth_area", support_level=support,
                                        segments=[segment], confidence=_CONFIDENCE[level], reviewer=REVIEWER))
                reason = f"Demo data: planted at the {_LEVEL_NAME[level]} level"
                observations.append(Observation(standard_code=code, paragraph=segment, reason=reason,
                                                severity=support, level=_OBSERVATION_LEVEL[a.type]))
                findings.append(Finding(
                    standard_code=code, concept=concept_of(code), description="", paragraphs=[segment],
                    reasons=[reason], observation_count=1, severity=support, support_level=support,
                    summary=f"Demo data, not a real review: a planted {_LEVEL_NAME[level]} growth area.",
                    confidence=_CONFIDENCE[level], pattern="consistent" if level >= 2 else "occasional",
                    skill_state="missing" if level == 3 else "misapplied",
                    condition=choices[_pick(row.student_hash + code, len(choices))] if choices else None))
                root = _root_of(code, shown, families)
                if root:
                    links.append(FindingLink(finding_code=code, root_code=root,
                                             rationale="Demo data: the same skill, traced to its lower-grade root."))
            for code in (c for c in evidenced if c not in shown):
                strong = code in a.targets and a.type != "multiple_choice" and _pick(row.student_hash + code, 2) == 0
                verdicts.append(Verdict(
                    standard_code=code, verdict="meets", reviewer=REVIEWER, notable_strength=strong,
                    strength_note="Demo data: strong evidence on this standard." if strong else None))
            yield ReviewResult(
                submission_ref=SubmissionRef(course_id=course_id, assignment_id=lms_assignment_id(a.id, version),
                                             user_id=row.student_hash, submission_id=row.submission_id, attempt=1,
                                             submitted_at=submitted.get(row.submission_id)),
                course_grade=course.grade, strategy="full", standards=[], observations=observations,
                findings=findings, met_standards=[], dropped=[], verification=Verification(status="disabled"),
                pipeline="v2", submission_type=a.type, verdicts=verdicts, links=links,
                models={REVIEWER: "answer-key"})


@dataclass(frozen=True)
class FillReport:
    saved: int
    already_there: int
    statuses: int
    needs_attention: int
    groups: int
    pairs: int


def _ensure_standards(conn, s: Settings, needed: set[str]) -> None:
    """Load the catalog if the demo's codes are missing. Fake embeddings: the v2 review doesn't use them."""
    have = {r[0] for r in conn.execute("SELECT code FROM standard").fetchall()}
    if needed - have:
        rows = loader.normalize_rows(loader.read_xlsx(s.standards_file))
        loader.load(conn, rows, FakeEmbedder(s.embedding_dim))


def fill_demo(s: Settings, through_week: int = 7, reset: bool = False) -> FillReport:
    families = load_families(Path(s.data_dir) / "skill_families.json")
    conditions = load_conditions(s.config_dir)
    results = {v: list(demo_results(s.data_dir, v, through_week, families, conditions)) for v in VERSIONS}
    conn = wiring.connect(s)
    try:
        _ensure_standards(conn, s, {v.standard_code for rs in results.values() for r in rs for v in r.verdicts})
        seed_course(conn, s.data_dir, VERSIONS[0])  # seeds the twin too
        if reset:
            for v in VERSIONS:
                reset_reviews(conn, dataset.course_id(v))
    finally:
        conn.close()
    writer = wiring.build_results_writer(s)
    saved = already = 0
    try:
        for rs in results.values():
            for r in rs:
                if writer.save(r, trigger="manual").status == "written":
                    saved += 1
                else:
                    already += 1
    finally:
        getattr(writer, "close", lambda: None)()
    cfg = from_effective(effective(load_defs(s.config_dir), []))
    reader, analytics = wiring.build_analytics_reader(s), wiring.build_analytics_writer(s)
    statuses = attention = groups = pairs = 0
    try:
        for v in VERSIONS:
            classroom = reader.classroom_for_course(dataset.course_id(v))
            out = analyze_classroom(reader, analytics, classroom, everything_reviewed(reader, classroom), cfg, "manual")
            statuses += len(out.statuses)
            attention += sum(st.status == "needs_attention" for st in out.statuses)
            groups += len(out.groups)
            pairs += len(out.pairs)
    finally:
        reader.close()
        analytics.close()
    return FillReport(saved, already, statuses, attention, groups, pairs)
