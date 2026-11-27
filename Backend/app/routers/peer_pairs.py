"""FR7: peer tutoring pairs. Justin's analysis suggests pairs (a student who is secure on a skill
tutors one who needs attention on it); the teacher approves or rejects each one.

Approving or rejecting is the backend's one write to Justin's tables, as his handoff doc says:
update peer_pair_suggestion (status, decided_by, decided_at). The decision survives later analyses.
Only anonymous student IDs and status words are returned: no names, no essay text, no percentages.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app import db
from app.access import require_classroom
from app.auth import CurrentUser, current_user
from app.routers.concepts import STATUS_LABELS, _skill_code

router = APIRouter(tags=["peer pairs"])

PAIR_LABELS = {"suggested": "Suggested", "approved": "Approved", "rejected": "Rejected"}

# One pair, with what the analysis currently says about each student on that skill, so the teacher
# sees why they were paired ("Secure" tutors "Needs attention").
_PAIR_SQL = """
    SELECT p.id AS pair_id, p.classroom_id, p.concept_code,
           meta.strand, meta.concept AS concept_name,
           p.tutor_id, tcs.status AS tutor_status,
           p.tutee_id, ecs.status AS tutee_status,
           p.status, p.decided_by, p.decided_at
    FROM peer_pair_suggestion p
    LEFT JOIN concept_status_latest tcs
           ON tcs.student_id = p.tutor_id AND tcs.classroom_id = p.classroom_id AND tcs.concept_code = p.concept_code
    LEFT JOIN concept_status_latest ecs
           ON ecs.student_id = p.tutee_id AND ecs.classroom_id = p.classroom_id AND ecs.concept_code = p.concept_code
    LEFT JOIN LATERAL (
        SELECT st.strand, st.concept FROM standard st
        WHERE st.code = p.concept_code OR st.code LIKE p.concept_code || '.%%'
        ORDER BY st.code LIMIT 1
    ) meta ON true
"""
_ORDER = {"suggested": 0, "approved": 1, "rejected": 2}


def _shape(r: dict) -> dict:
    return {
        "pair_id": str(r["pair_id"]),
        "classroom_id": r["classroom_id"],
        "concept_code": r["concept_code"],
        "skill_code": _skill_code(r["concept_code"]),
        "strand": r["strand"],
        "concept_name": r["concept_name"],
        "tutor": {"student_id": r["tutor_id"], "status": r["tutor_status"],
                  "status_label": STATUS_LABELS.get(r["tutor_status"])},
        "tutee": {"student_id": r["tutee_id"], "status": r["tutee_status"],
                  "status_label": STATUS_LABELS.get(r["tutee_status"])},
        "status": r["status"],
        "status_label": PAIR_LABELS[r["status"]],
        "decided_by": r["decided_by"],
        "decided_at": r["decided_at"],
    }


@router.get("/classrooms/{classroom_id}/peer-pairs")
def list_peer_pairs(
    classroom_id: str,
    pair_status: Literal["suggested", "approved", "rejected"] | None = Query(
        None, alias="status", description="Only pairs with this status"),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """The pairs from the latest analysis of this class, waiting ones first. `pending` is how many
    still need the teacher's decision (for the dashboard and notifications)."""
    require_classroom(user, classroom_id)
    rows = db.query(_PAIR_SQL + """
        JOIN peer_pair_latest l ON l.pair_id = p.id
        WHERE p.classroom_id = %s
    """, (classroom_id,))
    pairs = sorted((_shape(r) for r in rows),
                   key=lambda p: (_ORDER[p["status"]], p["concept_code"], p["tutee"]["student_id"], p["tutor"]["student_id"]))
    return {
        "classroom_id": classroom_id,
        "pending": sum(p["status"] == "suggested" for p in pairs),
        "pairs": [p for p in pairs if pair_status is None or p["status"] == pair_status],
    }


class Decision(BaseModel):
    status: Literal["approved", "rejected"]


@router.patch("/peer-pairs/{pair_id}")
def decide_peer_pair(pair_id: str, decision: Decision, user: CurrentUser = Depends(current_user)) -> dict:
    """Approve or reject a suggested pair. 404 if there's no such pair, 403 if it's another
    teacher's class, 409 if it was already decided (a decision is made once; a double click or a
    second teacher can't silently overwrite it)."""
    try:
        uuid.UUID(pair_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "peer pair not found") from None
    found = db.query("SELECT classroom_id, status FROM peer_pair_suggestion WHERE id = %s", (pair_id,))
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "peer pair not found")
    require_classroom(user, found[0]["classroom_id"])

    changed = db.execute("""
        UPDATE peer_pair_suggestion
           SET status = %s, decided_by = %s, decided_at = now()
         WHERE id = %s AND status = 'suggested'
     RETURNING id
    """, (decision.status, user.email, pair_id))
    if not changed:
        current = db.query("SELECT status FROM peer_pair_suggestion WHERE id = %s", (pair_id,))[0]["status"]
        raise HTTPException(status.HTTP_409_CONFLICT, f"this pair was already {current}")
    return _shape(db.query(_PAIR_SQL + " WHERE p.id = %s", (pair_id,))[0])
