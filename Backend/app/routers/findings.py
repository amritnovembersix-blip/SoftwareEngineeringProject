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
# ---------------------------------------------------------
# Student Learning Insights
# Additive endpoint
# ---------------------------------------------------------

_INSIGHTS_CTE = """
    WITH finding_rows AS (
        SELECT DISTINCT
            f.id AS finding_id,
            s.id AS submission_pk,
            s.submitted_at,
            st.code AS standard_code,
            st.strand,
            COALESCE(NULLIF(st.concept, ''), st.code) AS concept
        FROM submission s
        JOIN finding f
            ON f.submission_id = s.id
        JOIN finding_standard fs
            ON fs.finding_id = f.id
        JOIN standard st
            ON st.id = fs.standard_id
        WHERE s.student_id = %s
          AND s.review_status = 'succeeded'
    )
"""


@router.get("/students/{student_id}/insights")
def student_insights(student_id: str) -> dict:
    """
    Derive learning patterns and strand distribution
    from the student's actual Agentic review records.

    No grades or mastery scores are calculated.
    """

    # Verify student exists.
    if not db.query(
        "SELECT 1 FROM student WHERE id = %s",
        (student_id,),
    ):
        raise HTTPException(
            status_code=404,
            detail="student not found",
        )

    # Overall counts.
    summary_rows = db.query(
        _INSIGHTS_CTE + """
        SELECT
            COUNT(DISTINCT finding_id)::int AS finding_count,
            COUNT(DISTINCT submission_pk)::int AS submissions_with_findings,
            COUNT(DISTINCT standard_code)::int AS standard_count,
            COUNT(DISTINCT concept)::int AS concept_count
        FROM finding_rows
        """,
        (student_id,),
    )

    # Group repeated concepts across submissions.
    # A concept appearing in the latest submission but not
    # in earlier submissions is marked as newly observed.
    pattern_rows = db.query(
        _INSIGHTS_CTE + """,
        latest_submission AS (
            SELECT id
            FROM submission
            WHERE student_id = %s
              AND review_status = 'succeeded'
            ORDER BY
                submitted_at DESC NULLS LAST,
                attempt DESC
            LIMIT 1
        )
        SELECT
            strand,
            concept,
            STRING_AGG(
                DISTINCT standard_code,
                ', ' ORDER BY standard_code
            ) AS standard_codes,
            COUNT(DISTINCT finding_id)::int AS finding_count,
            COUNT(DISTINCT submission_pk)::int AS submission_count,
            CASE
                WHEN COUNT(DISTINCT submission_pk) > 1
                    THEN 'recurring'
                WHEN BOOL_OR(
                    submission_pk = (
                        SELECT id FROM latest_submission
                    )
                )
                    THEN 'new'
                ELSE 'single'
            END AS pattern_type,
            MAX(submitted_at) AS last_seen_at
        FROM finding_rows
        GROUP BY strand, concept
        ORDER BY finding_count DESC, concept
        """,
        (student_id, student_id),
    )

    # Derive chart data from actual findings grouped by strand.
    strand_rows = db.query(
        _INSIGHTS_CTE + """
        SELECT
            strand,
            COUNT(DISTINCT finding_id)::int AS finding_count,
            COUNT(DISTINCT submission_pk)::int AS submission_count
        FROM finding_rows
        WHERE strand IS NOT NULL
        GROUP BY strand
        ORDER BY finding_count DESC, strand
        """,
        (student_id,),
    )

    summary = summary_rows[0] if summary_rows else {
        "finding_count": 0,
        "submissions_with_findings": 0,
        "standard_count": 0,
        "concept_count": 0,
    }

    return {
        "summary": summary,
        "recurring_patterns": [
            row for row in pattern_rows
            if row["pattern_type"] == "recurring"
        ],
        "new_patterns": [
            row for row in pattern_rows
            if row["pattern_type"] == "new"
        ],
        "single_observation_patterns": [
            row for row in pattern_rows
            if row["pattern_type"] == "single"
        ],
        "strand_chart": strand_rows,
    }
