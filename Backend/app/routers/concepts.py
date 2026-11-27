"""FR6: a student's status on each skill (concept), and how it changed over their work.

Justin's analysis writes the status (`concept_status_latest`); the backend only reads it.
SASS doesn't grade, so nothing here is a score or a percentage: a status, a support level
(0 Meets, 1 Approaching, 2 Developing, 3 Beginning), an evidence count such as "2/3", and a trend.

A concept is the first four segments of a standard code (`concept_of` in the analysis): 3.L.1.A.e
and 3.L.1.A.g are both part of concept 3.L.1.A. The same skill appears at several grades (6.W.3.A, 7.W.3.A, ... 9-10.W.3.A),
so each concept also gets a `skill_code` without the grade (W.3.A) for grouping it once.
"""
from fastapi import APIRouter, Depends, Query

from app import db
from app.access import own_classrooms, require_student
from app.auth import CurrentUser, current_user

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
_CONCEPT_OF = "array_to_string((string_to_array(st.code, '.'))[1:4], '.')"  # first four segments, like the analysis
_SKILL_OF = r"regexp_replace({col}, '^[0-9]+(-[0-9]+)?\.', '')"


def _level_labels() -> dict[int, str]:
    """0..3 to the words the team agreed to show, read from Justin's display_label table."""
    rows = {r["code"]: r["label"] for r in db.query(
        "SELECT code, label FROM display_label WHERE (kind, code) IN "
        "(('verdict', 'meets'), ('support_level', 'approaching'), "
        " ('support_level', 'developing'), ('support_level', 'beginning'))")}
    return {0: rows.get("meets", "Meets"), 1: rows.get("approaching", "Approaching"),
            2: rows.get("developing", "Developing"), 3: rows.get("beginning", "Beginning")}


@router.get("/students/{student_id}/concepts")
def student_concepts(
    student_id: str,
    classroom_id: str | None = Query(None, description="Only this classroom"),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """The student's latest status on every concept, most urgent first.

    `summary` counts the concepts per status (for the badges at the top of the student page).
    `needs_attention` is the FR6 notification rule: Developing or Beginning on at least 2 of the
    last 3 pieces of work.
    """
    require_student(user, student_id)
    scope, scope_params = own_classrooms(user, "c.classroom_id")
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
        WHERE c.student_id = %s AND (%s::text IS NULL OR c.classroom_id = %s) AND {scope}
    """, (student_id, classroom_id, classroom_id, *scope_params))
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
    user: CurrentUser = Depends(current_user),
) -> list[dict]:
    """For each concept, the support level on every reviewed piece of work, ordered by due date,
    with the week it falls in (the due date's Monday in America/Chicago, like the class heatmap).
    For the progress chart.

    A piece of work's level for a concept is its weakest level across that concept's standards,
    ignoring standards marked "not evident", with the same rules as the analysis: only the attempt
    that counts, only work due by the analysis's `as_of`, a growth area the teacher's latest answer
    disagreed with or dismissed counts as meets, and a "meets" implied by meeting a higher grade is
    left out. This is the same series the analysis stores as `points`, so the chart and the status
    always agree.
    """
    require_student(user, student_id)
    scope, scope_params = own_classrooms(user, "a.classroom_id")
    rows = db.query(f"""
        WITH asof AS (  -- the analysis's cutoff, so the chart shows exactly the points behind the status
            SELECT classroom_id, max(as_of) AS as_of FROM concept_status_latest
            WHERE student_id = %s GROUP BY classroom_id)
        SELECT a.classroom_id, {_CONCEPT_OF} AS concept_code,
               s.lms_submission_id AS submission_id, a.id AS assignment_id, a.title AS assignment_title,
               s.submitted_at,
               date_trunc('week', COALESCE(a.due_at, s.submitted_at) AT TIME ZONE 'America/Chicago')::date
                   AS week_start,
               -- a growth area a teacher disagreed with or dismissed counts as meets (0), like the analysis
               COALESCE(MAX(CASE WHEN v.verdict = 'growth_area' AND NOT COALESCE(f.overruled, false)
                                 THEN {_LEVEL_SQL} END), 0) AS level
        FROM verdict v
        JOIN submission s ON s.id = v.submission_id
        JOIN assignment a ON a.id = s.assignment_id
        JOIN standard st  ON st.id = v.standard_id
        JOIN asof         ON asof.classroom_id = a.classroom_id
        LEFT JOIN LATERAL (
            -- the teacher's latest answer counts: disagree or dismiss overrules, a later agree restores
            SELECT (SELECT t.stance IN ('disagree', 'dismiss') FROM teacher_response t
                    WHERE t.finding_id = f.id ORDER BY t.responded_at DESC, t.id DESC LIMIT 1) AS overruled
            FROM finding f JOIN finding_standard fs ON fs.finding_id = f.id AND fs.standard_id = v.standard_id
            WHERE f.submission_id = s.id ORDER BY f.id LIMIT 1
        ) f ON true
        WHERE s.student_id = %s AND s.review_status = 'succeeded' AND s.superseded_at IS NULL
          AND COALESCE(a.due_at, s.submitted_at) <= asof.as_of
          -- "meets" implied by meeting a higher grade: nothing was observed at this grade
          AND NOT (v.verdict = 'meets' AND v.working_grade IS NOT NULL AND st.grade_min < v.working_grade)
          AND (%s::text IS NULL OR a.classroom_id = %s)
          AND {scope}
        GROUP BY a.classroom_id, concept_code, s.id, a.id
        HAVING bool_or(v.verdict <> 'not_evident') AND (%s::text IS NULL OR {_CONCEPT_OF} = %s)
        ORDER BY concept_code, COALESCE(a.due_at, s.submitted_at), s.submitted_at, s.id::text
    """, (student_id, student_id, classroom_id, classroom_id, *scope_params, concept_code, concept_code))
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
