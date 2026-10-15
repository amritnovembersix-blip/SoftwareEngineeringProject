"""Adapter: ResultsWriter over the lab Postgres schema (claim-persist protocol, see V4 comments).

Privacy: only reasons (<= 12 words) and template summaries are written; never essay text.
IDs: a ReviewResult carries LMS IDs; the SASS rows they belong to are found through the `lms_*`
columns (classroom.lms_course_id, assignment.lms_assignment_id, student.lms_user_id), so SASS keys
never have to equal LMS IDs. Those rows must exist first (seeded from the LMS).
Uses `with conn.transaction()` only (savepoints under a test transaction, real transactions
on an autocommit connection).
"""
from psycopg.types.json import Jsonb

from agentic.models import ReviewResult
from agentic.results.base import SaveOutcome

CHUNKER_VERSION = "mvp-whole-essay-1"

_SASS_IDS = """
SELECT a.id, (SELECT s.id FROM student s WHERE s.lms_user_id = %(user)s)
FROM assignment a JOIN classroom c ON c.id = a.classroom_id
WHERE a.lms_assignment_id = %(asg)s AND c.lms_course_id = %(course)s
"""

_CLAIM = """
INSERT INTO submission (lms_submission_id, attempt, assignment_id, student_id, submitted_at,
                        review_run_id, review_status)
VALUES (%(sub)s, %(attempt)s, %(asg)s, %(student)s, COALESCE(%(at)s, now()), %(run)s, 'in_progress')
ON CONFLICT ON CONSTRAINT submission_attempt_unique DO UPDATE SET
  review_run_id = EXCLUDED.review_run_id, review_status = 'in_progress', reviewed_at = NULL
WHERE submission.review_status = 'failed'
   OR (submission.review_status = 'in_progress' AND EXISTS (
         SELECT 1 FROM review_run r WHERE r.id = submission.review_run_id
           AND (r.status <> 'running' OR r.started_at < now() - interval '1 hour')))
RETURNING id
"""


class PostgresResultsWriter:
    def __init__(self, conn):
        self._conn = conn

    def close(self) -> None:
        self._conn.close()

    def is_reviewed(self, submission_id: str, attempt: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM submission WHERE lms_submission_id = %s AND attempt = %s "
            "AND review_status = 'succeeded'", (submission_id, attempt)).fetchone() is not None

    def save(self, result: ReviewResult, trigger: str = "manual") -> SaveOutcome:
        ref = result.submission_ref
        existing = self._conn.execute(
            "SELECT review_run_id::text FROM submission WHERE lms_submission_id = %s "
            "AND attempt = %s AND review_status = 'succeeded'",
            (ref.submission_id, ref.attempt)).fetchone()
        if existing:
            return SaveOutcome(status="duplicate", review_run_id=existing[0])
        with self._conn.transaction():
            run_id = self._conn.execute(
                "INSERT INTO review_run (run_type, trigger_type, status, scope, submission_count, started_at) "
                "VALUES ('submission_review', %s, 'running', %s, 1, now()) RETURNING id::text",
                (trigger, Jsonb({"course_id": ref.course_id}))).fetchone()[0]
        try:
            with self._conn.transaction():
                other = self._persist(result, run_id)
        except Exception as err:
            with self._conn.transaction():
                self._conn.execute(
                    "UPDATE review_run SET status = 'failed', error = %s, finished_at = now() WHERE id = %s",
                    (type(err).__name__, run_id))
            raise
        if other:
            with self._conn.transaction():
                self._conn.execute(
                    "UPDATE review_run SET status = 'failed', error = 'submission already claimed', "
                    "finished_at = now() WHERE id = %s", (run_id,))
            return SaveOutcome(status="duplicate", review_run_id=other)
        return SaveOutcome(status="written", review_run_id=run_id)

    def _persist(self, result: ReviewResult, run_id: str) -> str | None:
        """Returns the other run's id when the submission is claimed elsewhere, else None."""
        c = self._conn
        ref = result.submission_ref
        ids = c.execute(_SASS_IDS, {"user": ref.user_id, "asg": ref.assignment_id,
                                    "course": ref.course_id}).fetchone()
        if ids is None or ids[1] is None:  # message names no IDs: they are student identifiers
            raise LookupError("assignment or student not in the database; seed the LMS tables first")
        row = c.execute(_CLAIM, {"sub": ref.submission_id, "attempt": ref.attempt,
                                 "asg": ids[0], "student": ids[1],
                                 "at": ref.submitted_at, "run": run_id}).fetchone()
        if row is None:
            return c.execute("SELECT review_run_id::text FROM submission WHERE lms_submission_id = %s "
                             "AND attempt = %s", (ref.submission_id, ref.attempt)).fetchone()[0]
        sid = row[0]
        std_ids: dict[str, str] = {}

        def std(code: str) -> str:
            if code not in std_ids:
                r = c.execute("SELECT id FROM standard WHERE code = %s", (code,)).fetchone()
                if r is None:
                    raise LookupError("unknown standard code")
                std_ids[code] = r[0]
            return std_ids[code]

        obs_ids: list[tuple[str, int, str]] = []
        for o in result.observations:
            oid = c.execute(
                "INSERT INTO observation (submission_id, chunk_index, level, standard_id, reason) "
                "VALUES (%s, %s, 'paragraph', %s, %s) RETURNING id",
                (sid, o.paragraph - 1, std(o.standard_code), o.reason)).fetchone()[0]
            obs_ids.append((o.standard_code, o.paragraph, oid))
        for f in result.findings:
            fid = c.execute(
                "INSERT INTO finding (submission_id, summary, observation_count) VALUES (%s, %s, %s) "
                "RETURNING id", (sid, f.summary, f.observation_count)).fetchone()[0]
            for code, para, oid in obs_ids:
                if code == f.standard_code and para in f.paragraphs:
                    c.execute("INSERT INTO finding_observation (finding_id, observation_id) VALUES (%s, %s)",
                              (fid, oid))
            c.execute("INSERT INTO finding_standard (finding_id, standard_id) VALUES (%s, %s)",
                      (fid, std(f.standard_code)))
        c.execute("UPDATE submission SET review_status = 'succeeded', reviewed_at = now(), "
                  "chunker_version = %s WHERE id = %s", (CHUNKER_VERSION, sid))
        c.execute("UPDATE review_run SET status = 'succeeded', completed_count = 1, finished_at = now() "
                  "WHERE id = %s", (run_id,))
        return None
