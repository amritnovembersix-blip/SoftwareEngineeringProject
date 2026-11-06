"""Dev/demo tool: delete one classroom's review results so it can be reviewed again.

Never exposed over HTTP. In the product, findings are permanent (teachers annotate them, never delete
them). Keeps the standards, teacher, classroom, students and assignments.
"""


def reset_reviews(conn, lms_course_id: str) -> dict[str, int]:
    """Delete the submissions of the classroom with this LMS course ID, and everything hanging off
    them, in one transaction. Returns rows deleted per table."""
    counts: dict[str, int] = {}

    def delete(table: str, sql: str, params) -> None:
        counts[table] = conn.execute(f"DELETE FROM {table} WHERE {sql}", params).rowcount

    with conn.transaction():
        _delete_analytics(conn, lms_course_id, delete)
        subs = [r[0] for r in conn.execute(
            "SELECT s.id FROM submission s JOIN assignment a ON a.id = s.assignment_id "
            "JOIN classroom c ON c.id = a.classroom_id WHERE c.lms_course_id = %s", (lms_course_id,)).fetchall()]
        runs = [r[0] for r in conn.execute(
            "SELECT DISTINCT review_run_id FROM submission WHERE id = ANY(%s)", (subs,)).fetchall()]
        findings = "finding_id IN (SELECT id FROM finding WHERE submission_id = ANY(%s))"
        delete("finding_link", findings, (subs,))
        delete("verdict", "submission_id = ANY(%s)", (subs,))
        delete("teacher_response", findings, (subs,))
        delete("finding_observation", findings, (subs,))
        delete("finding_standard", findings, (subs,))
        delete("finding", "submission_id = ANY(%s)", (subs,))
        delete("observation", "submission_id = ANY(%s)", (subs,))
        delete("submission", "id = ANY(%s)", (subs,))
        # Runs that nothing else still points at (other classrooms' submissions, trend insights).
        delete("review_run", "id = ANY(%s) AND NOT EXISTS (SELECT 1 FROM submission s WHERE s.review_run_id = review_run.id) "
               "AND NOT EXISTS (SELECT 1 FROM trend_insight t WHERE t.review_run_id = review_run.id)", (runs,))
    return counts


def _delete_analytics(conn, lms_course_id: str, delete) -> None:
    """The classroom's analytics snapshots, its peer pairs, and its trend-analysis runs (found by
    scope->>'classroom_id'), children first."""
    row = conn.execute("SELECT id FROM classroom WHERE lms_course_id = %s", (lms_course_id,)).fetchone()
    cid = row[0] if row else None
    runs = "SELECT id FROM review_run WHERE run_type = 'trend_analysis' AND scope->>'classroom_id' = %s"
    pairs = "SELECT id FROM peer_pair_suggestion WHERE classroom_id = %s"
    delete("peer_pair_seen", f"review_run_id IN ({runs}) OR pair_id IN ({pairs})", (cid, cid))
    delete("peer_pair_suggestion", "classroom_id = %s", (cid,))
    delete("reteach_group_member",
           f"group_id IN (SELECT id FROM reteach_group WHERE classroom_id = %s OR review_run_id IN ({runs}))",
           (cid, cid))
    delete("reteach_group", f"classroom_id = %s OR review_run_id IN ({runs})", (cid, cid))
    delete("concept_status", f"classroom_id = %s OR review_run_id IN ({runs})", (cid, cid))
    delete("class_concept_week", f"classroom_id = %s OR review_run_id IN ({runs})", (cid, cid))
    delete("review_run", "run_type = 'trend_analysis' AND scope->>'classroom_id' = %s", (cid,))
