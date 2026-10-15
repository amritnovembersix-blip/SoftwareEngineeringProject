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
        subs = [r[0] for r in conn.execute(
            "SELECT s.id FROM submission s JOIN assignment a ON a.id = s.assignment_id "
            "JOIN classroom c ON c.id = a.classroom_id WHERE c.lms_course_id = %s", (lms_course_id,)).fetchall()]
        runs = [r[0] for r in conn.execute(
            "SELECT DISTINCT review_run_id FROM submission WHERE id = ANY(%s)", (subs,)).fetchall()]
        findings = "finding_id IN (SELECT id FROM finding WHERE submission_id = ANY(%s))"
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
