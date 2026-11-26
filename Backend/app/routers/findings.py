"""Screen 3: what the AI reviewer found in one student's essay."""
from fastapi import APIRouter, HTTPException, Query

from app import db

router = APIRouter(tags=["findings"])


@router.get("/students/{student_id}/findings")
def student_findings(
    student_id: str,
    assignment_id: str | None = Query(None, description="Only findings from this assignment"),
) -> list[dict]:
    """Each finding with its standard, its assignment and the paragraph-level evidence behind it.

    A student has many assignments, so each finding says which one it came from (newest first).
    Pass ?assignment_id= to get one assignment, e.g. the one shown on the students page.
    The essay text is never stored, so it is never returned: only the AI's short reasons.
    """
    if not db.query("SELECT 1 FROM student WHERE id = %s", (student_id,)):
        raise HTTPException(status_code=404, detail="student not found")
    return db.query("""
        SELECT f.id,
               f.summary,
               st.code        AS standard_code,
               st.strand      AS strand,
               st.concept     AS concept,
               st.description AS standard_description,
               s.lms_submission_id AS submission_id,
               s.attempt,
               a.id           AS assignment_id,
               a.title        AS assignment_title,
               s.submitted_at,
               COALESCE(
                 json_agg(json_build_object('paragraph', o.chunk_index + 1, 'reason', o.reason)
                          ORDER BY o.chunk_index) FILTER (WHERE o.id IS NOT NULL),
                 '[]') AS evidence
        FROM submission s
        JOIN assignment a        ON a.id = s.assignment_id
        JOIN finding f           ON f.submission_id = s.id
        JOIN finding_standard fs ON fs.finding_id = f.id
        JOIN standard st         ON st.id = fs.standard_id
        LEFT JOIN finding_observation fo ON fo.finding_id = f.id
        LEFT JOIN observation o  ON o.id = fo.observation_id
        WHERE s.student_id = %s AND s.review_status = 'succeeded' AND s.superseded_at IS NULL
          AND (%s::text IS NULL OR a.id = %s)
        GROUP BY f.id, st.id, s.id, a.id
        ORDER BY s.submitted_at DESC, st.code
    """, (student_id, assignment_id, assignment_id))
