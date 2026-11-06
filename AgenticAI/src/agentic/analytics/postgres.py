"""Adapters: ResultsReader and AnalyticsWriter over the lab Postgres schema (V4, V7, V8).

Privacy: reads only ids, codes, levels, booleans, confidences, dates and finding.condition; writes only
ids, concept codes, levels, counts and dates. No student text is selected or stored.
A verdict's due date is the assignment's due_at, or the submission time when the assignment has none.
Snapshots are INSERT-only: every run writes new rows under its own review_run (run_type 'trend_analysis').
Uses `with conn.transaction()` only (savepoints under a test transaction, real transactions on an
autocommit connection).
"""
from datetime import datetime

from psycopg.types.json import Jsonb

from agentic.analytics.base import AnalysisRun
from agentic.analytics.models import LinkRow, VerdictRow
from agentic.analytics.status import ConceptStatus
from agentic.analytics.summary import ClassWeek, PairSuggestion, ReteachGroup
from agentic.analytics.timeline import concept_code

_DUE = "COALESCE(a.due_at, s.submitted_at)"

_VERDICTS = f"""
SELECT s.student_id, a.classroom_id, s.id::text, {_DUE}, s.submitted_at,
       COALESCE(s.submission_type, a.submission_type), sd.code, v.verdict, v.support_level,
       v.notable_strength, v.confidence, f.condition, COALESCE(f.overruled, false),
       (v.verdict = 'meets' AND v.working_grade IS NOT NULL AND sd.grade_min < v.working_grade)
FROM verdict v
JOIN submission s ON s.id = v.submission_id
JOIN assignment a ON a.id = s.assignment_id
JOIN standard sd ON sd.id = v.standard_id
LEFT JOIN LATERAL (
  SELECT f.condition,
         EXISTS (SELECT 1 FROM teacher_response t
                 WHERE t.finding_id = f.id AND t.stance IN ('disagree', 'dismiss')) AS overruled
  FROM finding f JOIN finding_standard fs ON fs.finding_id = f.id AND fs.standard_id = v.standard_id
  WHERE f.submission_id = s.id
  ORDER BY f.id LIMIT 1) f ON true
WHERE a.classroom_id = %(classroom)s AND s.review_status = 'succeeded' AND s.superseded_at IS NULL
  AND {_DUE} <= %(as_of)s AND (%(student)s::text IS NULL OR s.student_id = %(student)s)
ORDER BY s.student_id, {_DUE}, s.id, sd.code
"""

_LINKS = f"""
SELECT DISTINCT s.student_id, s.id::text, gsd.code, rsd.code
FROM finding_link l
JOIN finding gf ON gf.id = l.finding_id
JOIN finding rf ON rf.id = l.root_finding_id
JOIN submission s ON s.id = gf.submission_id
JOIN assignment a ON a.id = s.assignment_id
JOIN finding_standard gfs ON gfs.finding_id = gf.id
JOIN standard gsd ON gsd.id = gfs.standard_id
JOIN finding_standard rfs ON rfs.finding_id = rf.id
JOIN standard rsd ON rsd.id = rfs.standard_id
WHERE a.classroom_id = %(classroom)s AND s.review_status = 'succeeded' AND s.superseded_at IS NULL
  AND {_DUE} <= %(as_of)s
ORDER BY 1, 2, 3, 4
"""


class PostgresResultsReader:
    def __init__(self, conn):
        self._conn = conn

    def close(self) -> None:
        self._conn.close()

    def latest_due(self, classroom_id: str) -> datetime | None:
        return self._conn.execute(
            f"SELECT max({_DUE}) FROM submission s JOIN assignment a ON a.id = s.assignment_id "
            "WHERE a.classroom_id = %s AND s.review_status = 'succeeded' AND s.superseded_at IS NULL",
            (classroom_id,)).fetchone()[0]

    def classroom_exists(self, classroom_id: str) -> bool:
        return self._conn.execute("SELECT 1 FROM classroom WHERE id = %s", (classroom_id,)).fetchone() is not None

    def classroom_for_course(self, lms_course_id: str) -> str | None:
        row = self._conn.execute("SELECT id FROM classroom WHERE lms_course_id = %s", (lms_course_id,)).fetchone()
        return row[0] if row else None

    def student_for_user(self, lms_user_id: str) -> str | None:
        row = self._conn.execute("SELECT id FROM student WHERE lms_user_id = %s", (lms_user_id,)).fetchone()
        return row[0] if row else None

    def verdict_rows(self, classroom_id: str, as_of: datetime,
                     student_id: str | None = None) -> list[VerdictRow]:
        cur = self._conn.execute(_VERDICTS, {"classroom": classroom_id, "as_of": as_of, "student": student_id})
        return [VerdictRow(student_id=r[0], classroom_id=r[1], submission_id=r[2], due_at=r[3],
                           submitted_at=r[4], submission_type=r[5], standard_code=r[6], verdict=r[7],
                           support_level=r[8], notable_strength=r[9], confidence=r[10],
                           finding_condition=r[11], overruled=r[12], implied=r[13]) for r in cur.fetchall()]

    def link_rows(self, classroom_id: str, as_of: datetime) -> list[LinkRow]:
        cur = self._conn.execute(_LINKS, {"classroom": classroom_id, "as_of": as_of})
        links = {LinkRow(student_id=r[0], submission_id=r[1], grade_concept=concept_code(r[2]),
                         root_concept=concept_code(r[3])) for r in cur.fetchall()}
        return sorted(links, key=lambda k: (k.student_id, k.submission_id, k.grade_concept, k.root_concept))


class PostgresAnalyticsWriter:
    def __init__(self, conn):
        self._conn = conn

    def close(self) -> None:
        self._conn.close()

    def start(self, run: AnalysisRun) -> None:
        scope = {"scope": run.scope, "classroom_id": run.classroom_id, "student_id": run.student_id,
                 "as_of": run.as_of.isoformat()}
        with self._conn.transaction():
            self._conn.execute(
                "INSERT INTO review_run (id, run_type, trigger_type, status, scope, started_at) "
                "VALUES (%s, 'trend_analysis', %s, 'running', %s, clock_timestamp())",
                (run.run_id, run.trigger, Jsonb(scope)))

    def save(self, run: AnalysisRun, statuses: list[ConceptStatus], weeks: list[ClassWeek],
             groups: list[ReteachGroup], pairs: list[PairSuggestion]) -> None:
        c = self._conn
        with c.transaction():
            for st in statuses:
                c.execute(
                    "INSERT INTO concept_status (review_run_id, student_id, classroom_id, concept_code, status, "
                    "current_level, basis, points, trend, as_of) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::smallint[], %s, %s)",
                    (run.run_id, st.student_id, st.classroom_id, st.concept_code, st.status, st.current_level,
                     st.basis, list(st.points), st.trend, run.as_of))
            for w in weeks:
                c.execute(
                    "INSERT INTO class_concept_week (review_run_id, classroom_id, concept_code, week_start, "
                    "n_level0, n_level1, n_level2, n_level3, n_too_little) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (run.run_id, w.classroom_id, w.concept_code, w.week_start, *w.counts, w.too_little))
            for g in groups:
                gid = c.execute(
                    "INSERT INTO reteach_group (review_run_id, classroom_id, root_concept, grade_concept) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (run.run_id, run.classroom_id, g.root_concept, g.grade_concept)).fetchone()[0]
                for student in g.students:
                    c.execute("INSERT INTO reteach_group_member (group_id, student_id) VALUES (%s, %s)",
                              (gid, student))
            for p in pairs:
                key = (run.classroom_id, p.concept_code, p.tutor_id, p.tutee_id)
                row = c.execute(
                    "INSERT INTO peer_pair_suggestion (classroom_id, concept_code, tutor_id, tutee_id) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (classroom_id, concept_code, tutor_id, tutee_id) "
                    "DO NOTHING RETURNING id", key).fetchone()
                pair_id = row[0] if row else c.execute(
                    "SELECT id FROM peer_pair_suggestion WHERE classroom_id = %s AND concept_code = %s "
                    "AND tutor_id = %s AND tutee_id = %s", key).fetchone()[0]
                # always, so peer_pair_latest lists a pair the teacher already decided, with that decision
                c.execute("INSERT INTO peer_pair_seen (review_run_id, pair_id) VALUES (%s, %s)",
                          (run.run_id, pair_id))
            c.execute("UPDATE review_run SET status = 'succeeded', finished_at = clock_timestamp() WHERE id = %s",
                      (run.run_id,))

    def fail(self, run: AnalysisRun, error: str) -> None:
        with self._conn.transaction():
            self._conn.execute(
                "UPDATE review_run SET status = 'failed', error = %s, finished_at = clock_timestamp() WHERE id = %s",
                (error, run.run_id))
