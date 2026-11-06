"""The team backend's findings query must keep working on v2-saved reviews (strengths are verdict rows, never
finding rows, so the Findings page lists growth areas only)."""
import pytest
from results.test_postgres_writer import H, _result, _v2_result, seeded, seeded3  # noqa: F401  (fixtures)

from agentic.results.postgres_writer import PostgresResultsWriter

pytestmark = pytest.mark.db

# Copied verbatim from Backend/app/routers/findings.py (student_findings) in the team repo
# amritnovembersix-blip/SoftwareEngineeringProject at main commit 2143bd176f7c0f17bc24529b49006c464b9e3543.
# If the team changes this query, update this copy and re-run.
BACKEND_FINDINGS_SQL = """
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
    """


def _run(conn):
    cur = conn.execute(BACKEND_FINDINGS_SQL, (H,))
    return [d.name for d in cur.description], cur.fetchall()


def test_backend_query_on_v2_returns_growth_areas_only_with_mvp_columns(seeded3):
    conn = seeded3
    w = PostgresResultsWriter(conn)
    w.save(_result(sub="sub-mvp"))
    mvp_cols, mvp_rows = _run(conn)
    assert [r[2] for r in mvp_rows] == ["t.1", "t.2"]
    # a second attempt, saved by v2 (the strength t.3 is a verdict row only)
    r2 = _v2_result(sub="sub-v2")
    r2 = r2.model_copy(update={"submission_ref": r2.submission_ref.model_copy(update={"attempt": 2})})
    w.save(r2)
    cols, rows = _run(conn)
    assert cols == mvp_cols
    v2_rows = [r for r in rows if r[6] == "sub-v2"]
    assert [r[2] for r in v2_rows] == ["t.1", "t.2"]  # t.3 never appears
    assert [e["paragraph"] for e in v2_rows[0][8]] == [1, 2]
