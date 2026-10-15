"""Screens 1 and 2: the classroom list and the students in one classroom.

Who is in a class comes from `enrollment`. A `submission` row only exists once an essay has been
reviewed, so it is joined in for review status and finding counts, never used to list students.
"""
from fastapi import APIRouter, HTTPException

from app import db

router = APIRouter(tags=["classrooms"])

# An enrollment counts while it has no end date or hasn't ended yet.
_ACTIVE = "(e.end_date IS NULL OR e.end_date >= CURRENT_DATE)"


@router.get("/classrooms")
def list_classrooms() -> list[dict]:
    """Every classroom, with how many students are enrolled and how many essays were reviewed."""
    return db.query(f"""
        SELECT c.id, c.name, c.grade_level, c.subject,
               (SELECT COUNT(*) FROM enrollment e
                 WHERE e.classroom_id = c.id AND {_ACTIVE}) AS student_count,
               (SELECT COUNT(*) FROM submission s
                  JOIN assignment a ON a.id = s.assignment_id
                 WHERE a.classroom_id = c.id AND s.review_status = 'succeeded') AS reviewed_count
        FROM classroom c
        ORDER BY c.name
    """)


@router.get("/classrooms/{classroom_id}/students")
def list_students(classroom_id: str) -> list[dict]:
    """Every student enrolled in this classroom. Students whose essay hasn't been reviewed yet are
    listed too, with review_status "not_reviewed" and 0 findings."""
    if not db.query("SELECT 1 FROM classroom WHERE id = %s", (classroom_id,)):
        raise HTTPException(status_code=404, detail="classroom not found")
    return db.query(f"""
        SELECT e.student_id,
               s.lms_submission_id AS submission_id,
               s.assignment_title,
               s.submitted_at,
               COALESCE(s.review_status, 'not_reviewed') AS review_status,
               COALESCE(s.finding_count, 0) AS finding_count
        FROM enrollment e
        LEFT JOIN LATERAL (
            -- this student's most recent reviewed-or-in-progress essay in this classroom
            SELECT sub.lms_submission_id, sub.submitted_at, sub.review_status, a.title AS assignment_title,
                   (SELECT COUNT(*) FROM finding f WHERE f.submission_id = sub.id) AS finding_count
            FROM submission sub
            JOIN assignment a ON a.id = sub.assignment_id
            WHERE a.classroom_id = e.classroom_id AND sub.student_id = e.student_id
            ORDER BY sub.submitted_at DESC, sub.attempt DESC
            LIMIT 1
        ) s ON true
        WHERE e.classroom_id = %s AND {_ACTIVE}
        ORDER BY e.student_id
    """, (classroom_id,))
