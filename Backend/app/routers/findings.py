"""Screen 3: what the AI reviewer found in one student's essay."""
from fastapi import APIRouter, HTTPException

from app import db

router = APIRouter(tags=["findings"])


@router.get("/students/{student_id}/findings")
def student_findings(student_id: str) -> list[dict]:
    """Each finding with its standard and the paragraph-level evidence behind it.

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
               COALESCE(
                 json_agg(json_build_object('paragraph', o.chunk_index + 1, 'reason', o.reason)
                          ORDER BY o.chunk_index) FILTER (WHERE o.id IS NOT NULL),
                 '[]') AS evidence
        FROM submission s
        JOIN finding f           ON f.submission_id = s.id
        JOIN finding_standard fs ON fs.finding_id = f.id
        JOIN standard st         ON st.id = fs.standard_id
        LEFT JOIN finding_observation fo ON fo.finding_id = f.id
        LEFT JOIN observation o  ON o.id = fo.observation_id
        WHERE s.student_id = %s AND s.review_status = 'succeeded'
        GROUP BY f.id, st.id, s.id
        ORDER BY st.code
    """, (student_id,))
