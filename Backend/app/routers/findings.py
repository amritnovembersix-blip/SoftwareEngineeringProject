"""Screen 3: what the AI reviewer found in a student's work, what they did well, and the teacher's
answer to each finding.

AI findings are drafts: the reviewer over-flags (Justin's handoff), so every finding carries its
confidence, and the teacher can agree, disagree or dismiss it. Confidence is shown as a word
(High, Medium, Low) next to the raw 0–1 value; it is never a grade and never a percentage.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app import db
from app.access import own_classrooms, require_classroom, require_student
from app.auth import CurrentUser, current_user

router = APIRouter(tags=["findings"])

SUPPORT_LABELS = {"approaching": "Approaching", "developing": "Developing", "beginning": "Beginning"}
STANCE_LABELS = {"agree": "Agreed", "disagree": "Disagreed", "dismiss": "Dismissed"}


def confidence_label(value: float | None) -> str | None:
    """0.86 -> High, 0.74 -> Medium, 0.62 -> Low (the three values the reviewer gives today)."""
    if value is None:
        return None
    if value >= 0.8:
        return "High"
    if value >= 0.7:
        return "Medium"
    return "Low"


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


@router.get("/students/{student_id}/findings")
def student_findings(
    student_id: str,
    assignment_id: str | None = Query(None, description="Only findings from this assignment"),
    user: CurrentUser = Depends(current_user),
) -> list[dict]:
    """Each finding with its standard, its assignment, the paragraph-level evidence behind it, how
    much support the student needs, how confident the reviewer is, and the teacher's latest answer.

    A student has many assignments, so each finding says which one it came from (newest first).
    Pass ?assignment_id= to get one assignment, e.g. the one shown on the students page.
    The essay text is never stored, so it is never returned: only the AI's short reasons.
    """
    require_student(user, student_id)
    scope, scope_params = own_classrooms(user, "a.classroom_id")
    rows = db.query(f"""
        SELECT f.id,
               f.summary,
               f.support_level,
               f.confidence,
               f.pattern,
               f.skill_state,
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
                 '[]') AS evidence,
               tr.stance      AS response_stance,
               tr.reason      AS response_reason,
               tr.responded_at
        FROM submission s
        JOIN assignment a        ON a.id = s.assignment_id
        JOIN finding f           ON f.submission_id = s.id
        JOIN finding_standard fs ON fs.finding_id = f.id
        JOIN standard st         ON st.id = fs.standard_id
        LEFT JOIN finding_observation fo ON fo.finding_id = f.id
        LEFT JOIN observation o  ON o.id = fo.observation_id
        LEFT JOIN LATERAL (
            SELECT r.stance, r.reason, r.responded_at FROM teacher_response r
            WHERE r.finding_id = f.id ORDER BY r.responded_at DESC LIMIT 1
        ) tr ON true
        WHERE s.student_id = %s AND s.review_status = 'succeeded' AND s.superseded_at IS NULL
          AND (%s::text IS NULL OR a.id = %s)
          AND {scope}
        GROUP BY f.id, st.id, s.id, a.id, tr.stance, tr.reason, tr.responded_at
        ORDER BY s.submitted_at DESC, st.code
    """, (student_id, assignment_id, assignment_id, *scope_params))
    for r in rows:
        r["support_level_label"] = SUPPORT_LABELS.get(r["support_level"])
        r["confidence"] = _round(r["confidence"])
        r["confidence_label"] = confidence_label(r["confidence"])
        r["is_draft"] = True  # every AI finding is a draft until the teacher answers it
        stance = r.pop("response_stance")
        r["teacher_response"] = None if stance is None else {
            "stance": stance,
            "stance_label": STANCE_LABELS[stance],
            "reason": r.pop("response_reason"),
            "responded_at": r.pop("responded_at"),
        }
        r.pop("response_reason", None)
        r.pop("responded_at", None)
    return rows


@router.get("/students/{student_id}/strengths")
def student_strengths(
    student_id: str,
    assignment_id: str | None = Query(None, description="Only strengths from this assignment"),
    user: CurrentUser = Depends(current_user),
) -> list[dict]:
    """What the student did well: standards the reviewer marked as a notable strength, with its
    short note, newest assignment first. Strengths live in `verdict`, never in `finding`."""
    require_student(user, student_id)
    scope, scope_params = own_classrooms(user, "a.classroom_id")
    rows = db.query(f"""
        SELECT st.code AS standard_code, st.strand, st.concept, st.description AS standard_description,
               v.strength_note, v.confidence,
               s.lms_submission_id AS submission_id, a.id AS assignment_id, a.title AS assignment_title,
               s.submitted_at
        FROM verdict v
        JOIN submission s ON s.id = v.submission_id
        JOIN assignment a ON a.id = s.assignment_id
        JOIN standard st  ON st.id = v.standard_id
        WHERE s.student_id = %s AND s.review_status = 'succeeded' AND v.notable_strength
          AND (%s::text IS NULL OR a.id = %s)
          AND {scope}
        ORDER BY s.submitted_at DESC, st.code
    """, (student_id, assignment_id, assignment_id, *scope_params))
    for r in rows:
        r["confidence"] = _round(r["confidence"])
        r["confidence_label"] = confidence_label(r["confidence"])
    return rows


class Response(BaseModel):
    stance: Literal["agree", "disagree", "dismiss"]
    reason: str | None = Field(None, max_length=500, description="Optional note, e.g. why you disagree")


@router.post("/findings/{finding_id}/responses", status_code=201)
def respond_to_finding(finding_id: str, body: Response, user: CurrentUser = Depends(current_user)) -> dict:
    """The teacher agrees, disagrees with or dismisses a finding. Findings themselves never change:
    each answer is a new teacher_response row, the latest one counts, and the next analysis treats a
    disagreed or dismissed finding as "meets" (Justin's rule).

    Only teachers answer (an admin has no class): 403 for admins and for another teacher's student.
    """
    try:
        uuid.UUID(finding_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found") from None
    found = db.query("""
        SELECT a.classroom_id FROM finding f
        JOIN submission s ON s.id = f.submission_id
        JOIN assignment a ON a.id = s.assignment_id
        WHERE f.id = %s
    """, (finding_id,))
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    if user.is_admin or not user.teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the class's teacher can answer a finding")
    require_classroom(user, found[0]["classroom_id"])

    reason = (body.reason or "").strip() or None
    row = db.execute("""
        INSERT INTO teacher_response (finding_id, teacher_id, stance, reason)
        VALUES (%s, %s, %s, %s)
        RETURNING id, finding_id, stance, reason, responded_at
    """, (finding_id, user.teacher_id, body.stance, reason))[0]
    return {
        "response_id": str(row["id"]),
        "finding_id": str(row["finding_id"]),
        "stance": row["stance"],
        "stance_label": STANCE_LABELS[row["stance"]],
        "reason": row["reason"],
        "responded_at": row["responded_at"],
    }
