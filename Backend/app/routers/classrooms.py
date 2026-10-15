"""Screens 1 and 2: the classroom list and the students in one classroom."""
from fastapi import APIRouter, HTTPException

from app import db

router = APIRouter(tags=["classrooms"])


@router.get("/classrooms")
def list_classrooms() -> list[dict]:
    """Every classroom, with how many students submitted and how many essays were reviewed."""
    return db.query("""
        SELECT c.id, c.name, c.grade_level, c.subject,
               COUNT(DISTINCT s.student_id) AS student_count,
               COUNT(DISTINCT s.id) FILTER (WHERE s.review_status = 'succeeded') AS reviewed_count
        FROM classroom c
        LEFT JOIN assignment a ON a.classroom_id = c.id
        LEFT JOIN submission s ON s.assignment_id = a.id
        GROUP BY c.id
        ORDER BY c.name
    """)


@router.get("/classrooms/{classroom_id}/students")
def list_students(classroom_id: str) -> list[dict]:
    """Students who submitted an essay in this classroom, with their review status and finding count."""
    if not db.query("SELECT 1 FROM classroom WHERE id = %s", (classroom_id,)):
        raise HTTPException(status_code=404, detail="classroom not found")
    return db.query("""
        SELECT s.student_id,
               s.lms_submission_id AS submission_id,
               a.title AS assignment_title,
               s.submitted_at,
               s.review_status,
               COUNT(f.id) AS finding_count
        FROM submission s
        JOIN assignment a ON a.id = s.assignment_id
        LEFT JOIN finding f ON f.submission_id = s.id
        WHERE a.classroom_id = %s
        GROUP BY s.id, a.title
        ORDER BY s.student_id
    """, (classroom_id,))
