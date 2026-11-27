"""Class-level views from Justin's analysis: the skills × weeks heatmap, reteach groups, and a
side-by-side comparison of two classes (e.g. Period 1 vs Period 2).

All numbers are counts of students, never percentages or scores: SASS doesn't grade. Levels use the
same 0–3 scale as everywhere else (0 Meets, 1 Approaching, 2 Developing, 3 Beginning). "Needing
support" means Developing or Beginning.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import db
from app.access import require_classroom
from app.auth import CurrentUser, current_user
from app.routers.concepts import _level_labels, _skill_code

router = APIRouter(tags=["class views"])

_META = """
    LEFT JOIN LATERAL (
        SELECT st.strand, st.concept FROM standard st
        WHERE st.code = {col} OR st.code LIKE {col} || '.%%'
        ORDER BY st.code LIMIT 1
    ) {alias} ON true
"""


@router.get("/classrooms/{classroom_id}/concept-weeks")
def concept_weeks(
    classroom_id: str,
    strand: str | None = Query(None, description="Only skills in this strand, e.g. Writing"),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """The heatmap: one row per skill, one column per week (weeks start on Monday). Each cell counts
    the class's students at each level that week, plus those with too little evidence so far.
    Rows needing the most support in the latest week come first."""
    require_classroom(user, classroom_id)
    rows = db.query("""
        SELECT w.concept_code, meta.strand, meta.concept AS concept_name, w.week_start,
               w.n_level0, w.n_level1, w.n_level2, w.n_level3, w.n_too_little
        FROM class_concept_week_latest w
    """ + _META.format(col="w.concept_code", alias="meta") + """
        WHERE w.classroom_id = %s AND (%s::text IS NULL OR meta.strand = %s)
        ORDER BY w.concept_code, w.week_start
    """, (classroom_id, strand, strand))
    weeks = sorted({r["week_start"] for r in rows})
    labels = _level_labels()
    skills: dict[str, dict] = {}
    for r in rows:
        s = skills.setdefault(r["concept_code"], {
            "concept_code": r["concept_code"],
            "skill_code": _skill_code(r["concept_code"]),
            "strand": r["strand"],
            "concept_name": r["concept_name"],
            "cells": {},
        })
        s["cells"][r["week_start"]] = {
            "week_start": r["week_start"],
            "meets": r["n_level0"], "approaching": r["n_level1"],
            "developing": r["n_level2"], "beginning": r["n_level3"],
            "too_little_evidence": r["n_too_little"],
            "needing_support": r["n_level2"] + r["n_level3"],
        }
    out = []
    for s in skills.values():
        s["cells"] = [s["cells"].get(w) for w in weeks]          # aligned with `weeks`; null = no data
        latest = next((c for c in reversed(s["cells"]) if c), None)
        s["needing_support_latest"] = latest["needing_support"] if latest else 0
        out.append(s)
    out.sort(key=lambda s: (-s["needing_support_latest"], s["concept_code"]))
    return {
        "classroom_id": classroom_id,
        "weeks": weeks,
        "legend": {"meets": labels[0], "approaching": labels[1], "developing": labels[2],
                   "beginning": labels[3], "too_little_evidence": "Too little evidence"},
        "skills": out,
    }


@router.get("/classrooms/{classroom_id}/reteach-groups")
def reteach_groups(classroom_id: str, user: CurrentUser = Depends(current_user)) -> list[dict]:
    """Students who share the same root gap: a grade-level skill they need support on that traces
    back to the same lower-grade skill. Teach the root skill to the group once. Largest groups first."""
    require_classroom(user, classroom_id)
    rows = db.query("""
        SELECT g.group_id, g.root_concept, rm.strand AS root_strand, rm.concept AS root_concept_name,
               g.grade_concept, gm.strand AS grade_strand, gm.concept AS grade_concept_name,
               array_agg(g.student_id ORDER BY g.student_id) AS student_ids
        FROM reteach_group_latest g
    """ + _META.format(col="g.root_concept", alias="rm") + _META.format(col="g.grade_concept", alias="gm") + """
        WHERE g.classroom_id = %s
        GROUP BY g.group_id, g.root_concept, rm.strand, rm.concept, g.grade_concept, gm.strand, gm.concept
    """, (classroom_id,))
    for r in rows:
        r["group_id"] = str(r["group_id"])
        r["root_skill_code"] = _skill_code(r["root_concept"])
        r["grade_skill_code"] = _skill_code(r["grade_concept"])
        r["size"] = len(r["student_ids"])
    rows.sort(key=lambda r: (-r["size"], r["grade_concept"], r["root_concept"]))
    return rows


def _class_summary(classroom_id: str) -> dict:
    head = db.query("SELECT id AS classroom_id, name, grade_level FROM classroom WHERE id = %s", (classroom_id,))[0]
    statuses = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM concept_status_latest WHERE classroom_id = %s GROUP BY status",
        (classroom_id,))}
    trends = {r["trend"]: r["n"] for r in db.query(
        "SELECT trend, COUNT(*) AS n FROM concept_status_latest WHERE classroom_id = %s GROUP BY trend",
        (classroom_id,))}
    counts = db.query("""
        SELECT (SELECT COUNT(*) FROM enrollment e WHERE e.classroom_id = %s
                  AND (e.end_date IS NULL OR e.end_date >= CURRENT_DATE)) AS student_count,
               (SELECT COUNT(DISTINCT student_id) FROM concept_status_latest
                 WHERE classroom_id = %s AND status = 'needs_attention') AS students_needing_attention
    """, (classroom_id, classroom_id))[0]
    weekly = db.query("""
        SELECT week_start, SUM(n_level2 + n_level3)::int AS needing_support, SUM(n_level0)::int AS meets
        FROM class_concept_week_latest WHERE classroom_id = %s
        GROUP BY week_start ORDER BY week_start
    """, (classroom_id,))
    return {
        **head, **counts,
        "skill_statuses": {k: statuses.get(k, 0) for k in
                           ("needs_attention", "emerging", "too_little_evidence", "secure")},
        "skill_trends": {k: trends.get(k, 0) for k in ("improving", "steady", "declining", "unclear")},
        "weekly": weekly,
    }


@router.get("/comparison")
def comparison(
    classrooms: str = Query(..., description="Two classroom IDs separated by a comma"),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """Two classes side by side, e.g. Period 1 (control) vs Period 2 (intervention): how many
    students need attention, how their skill statuses and trends split, and, week by week, how many
    student-skill results needed support. Counts only; the classes report their sizes so the reader
    can judge them fairly."""
    ids = [c.strip() for c in classrooms.split(",") if c.strip()]
    if len(ids) != 2 or ids[0] == ids[1]:
        raise HTTPException(422, "give exactly two different classroom IDs")
    for cid in ids:
        require_classroom(user, cid)
    a, b = (_class_summary(cid) for cid in ids)
    return {"classrooms": [a, b]}
