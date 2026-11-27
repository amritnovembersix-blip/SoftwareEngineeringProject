"""The teacher's home page: one summary card per class, and the notifications built from it.

Both are read-only and computed on each request from Justin's analysis (concept_status_latest,
peer_pair_latest) and the review records, so they can never go stale or disagree with the class
pages. A teacher sees only their own classes; an admin sees all. Counts of students, never scores.
"""
from fastapi import APIRouter, Depends

from app import db
from app.access import classroom_filter
from app.auth import CurrentUser, current_user

router = APIRouter(tags=["dashboard"])

_ACTIVE = "(e.end_date IS NULL OR e.end_date >= CURRENT_DATE)"


def _class_summaries(user: CurrentUser) -> list[dict]:
    where, params = classroom_filter(user)
    return db.query(f"""
        SELECT c.id AS classroom_id, c.name, c.grade_level,
               (SELECT COUNT(*) FROM enrollment e WHERE e.classroom_id = c.id AND {_ACTIVE}) AS student_count,
               (SELECT COUNT(DISTINCT cs.student_id) FROM concept_status_latest cs
                 WHERE cs.classroom_id = c.id AND cs.status = 'needs_attention') AS students_needing_attention,
               (SELECT COALESCE(array_agg(DISTINCT cs.student_id ORDER BY cs.student_id), '{{}}')
                  FROM concept_status_latest cs
                 WHERE cs.classroom_id = c.id AND cs.status = 'needs_attention') AS attention_student_ids,
               (SELECT COUNT(*) FROM peer_pair_latest p
                 WHERE p.classroom_id = c.id AND p.status = 'suggested') AS pending_pairs,
               (SELECT COUNT(*) FROM submission s JOIN assignment a ON a.id = s.assignment_id
                 WHERE a.classroom_id = c.id AND s.review_status = 'failed') AS failed_reviews,
               (SELECT MAX(s.reviewed_at) FROM submission s JOIN assignment a ON a.id = s.assignment_id
                 WHERE a.classroom_id = c.id AND s.review_status = 'succeeded') AS last_reviewed_at,
               (SELECT MAX(cs.as_of) FROM concept_status_latest cs WHERE cs.classroom_id = c.id) AS analysis_as_of
        FROM classroom c
        WHERE {where}
        ORDER BY c.name
    """, params)


@router.get("/dashboard")
def dashboard(user: CurrentUser = Depends(current_user)) -> dict:
    """One card per class the user can see, plus totals for the top of the page:
    students needing attention (Developing or Beginning on 2 of their last 3 pieces of work on some
    skill), peer pairs waiting for a decision, failed reviews, and when the class was last reviewed."""
    classes = _class_summaries(user)
    for c in classes:
        c.pop("attention_student_ids")
    return {
        "user": {"display_name": user.display_name, "role": user.role},
        "totals": {
            "classrooms": len(classes),
            "students": sum(c["student_count"] for c in classes),
            "students_needing_attention": sum(c["students_needing_attention"] for c in classes),
            "pending_pairs": sum(c["pending_pairs"] for c in classes),
            "failed_reviews": sum(c["failed_reviews"] for c in classes),
        },
        "classrooms": classes,
    }


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


@router.get("/notifications")
def notifications(user: CurrentUser = Depends(current_user)) -> list[dict]:
    """What needs the teacher's attention now, most urgent first. Built from the same numbers as
    the dashboard, so the bell and the cards always agree. A class with nothing to report gets no
    notification. Each one has a stable `id` (kind + class) so the page can hide ones already seen."""
    out = []
    for c in _class_summaries(user):
        name, cid = c["name"], c["classroom_id"]
        if c["failed_reviews"]:
            out.append({
                "id": f"failed_reviews:{cid}", "kind": "failed_reviews", "classroom_id": cid,
                "classroom_name": name, "count": c["failed_reviews"],
                "message": f"{_plural(c['failed_reviews'], 'essay', 'essays')} could not be reviewed in {name}.",
                "student_ids": [],
            })
        if c["students_needing_attention"]:
            n = c["students_needing_attention"]
            out.append({
                "id": f"needs_attention:{cid}", "kind": "needs_attention", "classroom_id": cid,
                "classroom_name": name, "count": n,
                "message": f"{_plural(n, 'student needs', 'students need')} attention in {name}.",
                "student_ids": list(c["attention_student_ids"]),
            })
        if c["pending_pairs"]:
            n = c["pending_pairs"]
            out.append({
                "id": f"pending_pairs:{cid}", "kind": "pending_pairs", "classroom_id": cid,
                "classroom_name": name, "count": n,
                "message": f"{_plural(n, 'peer tutoring pair is', 'peer tutoring pairs are')} waiting for your decision in {name}.",
                "student_ids": [],
            })
    order = {"failed_reviews": 0, "needs_attention": 1, "pending_pairs": 2}
    return sorted(out, key=lambda n: (order[n["kind"]], n["classroom_name"]))
