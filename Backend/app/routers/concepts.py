"""FR6: a student's status on each skill (concept), and how it changed over their work.

Justin's analysis writes the status (`concept_status_latest`); the backend only reads it.
SASS doesn't grade, so nothing here is a score or a percentage: a status, a support level
(0 Meets, 1 Approaching, 2 Developing, 3 Beginning), an evidence count such as "2/3", and a trend.

A concept is a standard code without its last lowercase letter: 3.L.1.A.e and 3.L.1.A.g are both
part of concept 3.L.1.A. The same skill appears at several grades (6.W.3.A, 7.W.3.A, ... 9-10.W.3.A),
so each concept also gets a `skill_code` without the grade (W.3.A) for grouping it once.
"""
from fastapi import APIRouter, HTTPException, Query

from app import db

router = APIRouter(tags=["students"])

# Most urgent first, so the teacher sees what needs attention at the top of the page.
_STATUS_ORDER = ["needs_attention", "emerging", "too_little_evidence", "secure"]
STATUS_LABELS = {
    "needs_attention": "Needs attention",
    "emerging": "Emerging",
    "too_little_evidence": "Too little evidence",
    "secure": "Secure",
}
# A verdict's support level as a number, the same scale the analysis uses (0 = meets the standard).
_LEVEL_SQL = """CASE v.support_level WHEN 'beginning' THEN 3 WHEN 'developing' THEN 2
                                     WHEN 'approaching' THEN 1 ELSE 0 END"""
_CONCEPT_OF = r"regexp_replace(st.code, '\.[a-z]$', '')"
_SKILL_OF = r"regexp_replace({col}, '^[0-9]+(-[0-9]+)?\.', '')"


def _level_labels() -> dict[int, str]:
    """0..3 to the words the team agreed to show, read from Justin's display_label table."""
    rows = {r["code"]: r["label"] for r in db.query(
        "SELECT code, label FROM display_label WHERE (kind, code) IN "
        "(('verdict', 'meets'), ('support_level', 'approaching'), "
        " ('support_level', 'developing'), ('support_level', 'beginning'))")}
    return {0: rows.get("meets", "Meets"), 1: rows.get("approaching", "Approaching"),
            2: rows.get("developing", "Developing"), 3: rows.get("beginning", "Beginning")}


def _require_student(student_id: str) -> None:
    if not db.query("SELECT 1 FROM student WHERE id = %s", (student_id,)):
        raise HTTPException(status_code=404, detail="student not found")


@router.get("/students/{student_id}/concepts")
def student_concepts(
    student_id: str,
    classroom_id: str | None = Query(None, description="Only this classroom"),
) -> dict:
    """The student's latest status on every concept, most urgent first.

    `summary` counts the concepts per status (for the badges at the top of the student page).
    `needs_attention` is the FR6 notification rule: Developing or Beginning on at least 2 of the
    last 3 pieces of work.
    """
    _require_student(student_id)
    rows = db.query(f"""
        SELECT c.classroom_id, c.concept_code,
               {_SKILL_OF.format(col='c.concept_code')} AS skill_code,
               meta.grade, meta.strand, meta.concept AS concept_name,
               c.status, c.current_level, c.basis, c.trend,
               cardinality(c.points) AS pieces_of_work,
               c.as_of
        FROM concept_status_latest c
        LEFT JOIN LATERAL (
            SELECT st.grade, st.strand, st.concept FROM standard st
            WHERE st.code = c.concept_code OR st.code LIKE c.concept_code || '.%%'
            ORDER BY st.code LIMIT 1
        ) meta ON true
        WHERE c.student_id = %s AND (%s::text IS NULL OR c.classroom_id = %s)
    """, (student_id, classroom_id, classroom_id))
    levels = _level_labels()
    for r in rows:
        r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
        r["level_label"] = levels.get(r["current_level"])
    rows.sort(key=lambda r: (_STATUS_ORDER.index(r["status"]) if r["status"] in _STATUS_ORDER else 9,
                             r["concept_code"]))
    summary = {s: sum(r["status"] == s for r in rows) for s in _STATUS_ORDER}
    return {
        "student_id": student_id,
        "as_of": max((r["as_of"] for r in rows), default=None),
        "summary": summary,
        "concepts": rows,
    }


@router.get("/students/{student_id}/concept-history")
def student_concept_history(
    student_id: str,
    concept_code: str | None = Query(None, description="Only this concept, e.g. 9-10.W.3.A"),
    classroom_id: str | None = Query(None, description="Only this classroom"),
) -> list[dict]:
    """For each concept, the support level on every reviewed piece of work, oldest first, with
    the week it falls in (weeks start on Monday, like the class heatmap). For the progress chart.

    A piece of work's level for a concept is its weakest level across that concept's standards,
    ignoring standards marked "not evident". This is the same series the analysis stores as
    `points`, so the chart and the status always agree.
    """
    _require_student(student_id)
    rows = db.query(f"""
        SELECT a.classroom_id, {_CONCEPT_OF} AS concept_code,
               s.lms_submission_id AS submission_id, a.id AS assignment_id, a.title AS assignment_title,
               s.submitted_at, date_trunc('week', s.submitted_at)::date AS week_start,
               MAX({_LEVEL_SQL}) AS level
        FROM verdict v
        JOIN submission s ON s.id = v.submission_id
        JOIN assignment a ON a.id = s.assignment_id
        JOIN standard st  ON st.id = v.standard_id
        WHERE s.student_id = %s AND s.review_status = 'succeeded' AND v.verdict <> 'not_evident'
          AND (%s::text IS NULL OR a.classroom_id = %s)
        GROUP BY a.classroom_id, concept_code, s.id, a.id
        HAVING (%s::text IS NULL OR {_CONCEPT_OF} = %s)
        ORDER BY concept_code, s.submitted_at, s.id
    """, (student_id, classroom_id, classroom_id, concept_code, concept_code))
    levels = _level_labels()
    out: dict[tuple, dict] = {}
    for r in rows:
        key = (r["classroom_id"], r["concept_code"])
        entry = out.setdefault(key, {
            "classroom_id": r["classroom_id"],
            "concept_code": r["concept_code"],
            "skill_code": _skill_code(r["concept_code"]),
            "points": [],
        })
        entry["points"].append({
            "submission_id": r["submission_id"],
            "assignment_id": r["assignment_id"],
            "assignment_title": r["assignment_title"],
            "submitted_at": r["submitted_at"],
            "week_start": r["week_start"],
            "level": r["level"],
            "level_label": levels.get(r["level"]),
        })
    return list(out.values())


def _skill_code(concept_code: str) -> str:
    """9-10.W.3.A -> W.3.A, 3.L.1.A -> L.1.A (the same skill at any grade)."""
    head, _, rest = concept_code.partition(".")
    return rest if head.replace("-", "").isdigit() else concept_code
