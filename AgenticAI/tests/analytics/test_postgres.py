from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from agentic.analytics.base import AnalysisRun, AnalyticsWriter, ResultsReader
from agentic.analytics.postgres import PostgresAnalyticsWriter, PostgresResultsReader
from agentic.analytics.run import UnknownClassroom, analyze_classroom, analyze_student
from agentic.analytics.status import AnalyticsSettings
from agentic.analytics.summary import PairSuggestion
from agentic.results.reset import reset_reviews

pytestmark = pytest.mark.db

S = AnalyticsSettings(attention_hits=2, attention_window=3, secure_hits=3, secure_window=4,
                      trend_window=3, review_flip_rate=0.10, group_min_size=2, max_tutees=2)
CID = "tr-c1"
GRADE_STD = "9-10.W.3.A.a"
ROOT_STD = "7.W.3.A.a"
CONCEPT = "9-10.W.3.A"
T0 = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
AS_OF = T0 + timedelta(days=60)


def _n(conn, table, where="true", params=()):
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", params).fetchone()[0]


class World:
    """Fixture rows for one classroom, written straight to the tables (inside the test transaction)."""

    def __init__(self, conn):
        self.conn = conn
        conn.execute("INSERT INTO teacher (id, name) VALUES ('tr-t', 'T') ON CONFLICT (id) DO NOTHING")
        conn.execute("INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name) "
                     "VALUES (%s, %s, 'tr-t', 'ela', 9, 'TR')", (CID, CID))
        for code, grade in ((GRADE_STD, 9), (ROOT_STD, 7)):
            conn.execute("INSERT INTO standard (code, grade, grade_min, grade_max, strand_code, strand, description) "
                         "VALUES (%s, %s, %s, %s, 'W', 'Writing', 'd') ON CONFLICT (code) DO NOTHING",
                         (code, str(grade), grade, grade))
        self.std = {c: conn.execute("SELECT id FROM standard WHERE code = %s", (c,)).fetchone()[0]
                    for c in (GRADE_STD, ROOT_STD)}
        self.run = conn.execute("INSERT INTO review_run (run_type, trigger_type, status) "
                                "VALUES ('submission_review', 'manual', 'succeeded') RETURNING id").fetchone()[0]
        self.students: set[str] = set()
        self.assignments: dict[int, str] = {}
        self.findings: dict[tuple[str, int], str] = {}
        self.submissions: dict[tuple[str, int], str] = {}

    def assignment(self, n, due="default"):
        aid = f"tr-a{n}"
        if due == "default":
            due = T0 + timedelta(days=7 * n)
        self.conn.execute("INSERT INTO assignment (id, lms_assignment_id, classroom_id, title, due_at, "
                          "submission_type) VALUES (%s, %s, %s, 'A', %s, 'essay')", (aid, aid, CID, due))
        self.assignments[n] = aid
        return aid

    def student(self, sid):
        if sid not in self.students:
            self.conn.execute("INSERT INTO student (id, lms_user_id) VALUES (%s, %s)", (sid, sid))
            self.conn.execute("INSERT INTO enrollment (classroom_id, student_id, start_date) VALUES (%s, %s, %s)",
                              (CID, sid, T0.date()))
            self.students.add(sid)

    def submit(self, sid, n, level, strength=False, attempt=1, status="succeeded", superseded=False,
               root=False, submitted_at=None):
        """A reviewed submission with one verdict on the grade standard (level 0 meets, else a growth
        area with a finding). `root` also adds a root finding on the lower-grade standard and links them."""
        self.student(sid)
        c = self.conn
        sub = f"tr-{sid}-{n}-{attempt}"
        sub_id = c.execute(
            "INSERT INTO submission (lms_submission_id, attempt, assignment_id, student_id, submitted_at, "
            "review_run_id, review_status, reviewed_at, superseded_at, submission_type) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'succeeded' THEN now() END, "
            "CASE WHEN %s THEN now() END, 'essay') RETURNING id",
            (sub, attempt, self.assignments[n], sid, submitted_at or T0 + timedelta(days=7 * n), self.run,
             status, status, superseded)).fetchone()[0]
        self.submissions[(sid, n)] = sub_id
        c.execute(
            "INSERT INTO verdict (submission_id, standard_id, verdict, support_level, notable_strength, "
            "strength_note, confidence) VALUES (%s, %s, %s, %s, %s, %s, 0.8)",
            (sub_id, self.std[GRADE_STD], "growth_area" if level else "meets",
             {1: "approaching", 2: "developing", 3: "beginning"}.get(level), strength,
             "Strong thesis" if strength else None))
        if level:
            self.findings[(sid, n)] = self._finding(sub_id, GRADE_STD, "with a prompt")
            if root:
                rid = self._finding(sub_id, ROOT_STD, None)
                c.execute("INSERT INTO finding_link (finding_id, root_finding_id, rationale) "
                          "VALUES (%s, %s, 'Builds on it')", (self.findings[(sid, n)], rid))
        return sub_id

    def _finding(self, sub_id, code, condition):
        fid = self.conn.execute(
            "INSERT INTO finding (submission_id, summary, observation_count, condition) "
            "VALUES (%s, 'Needs work', 1, %s) RETURNING id", (sub_id, condition)).fetchone()[0]
        self.conn.execute("INSERT INTO finding_standard (finding_id, standard_id) VALUES (%s, %s)",
                          (fid, self.std[code]))
        return fid

    def respond(self, sid, n, stance):
        self.conn.execute("INSERT INTO teacher_response (finding_id, teacher_id, stance) VALUES (%s, 'tr-t', %s)",
                          (self.findings[(sid, n)], stance))

    def standard_class(self):
        """a, b, c: growth areas on three assignments (a and b link to the lower-grade root); d: meets
        four times with a strength."""
        for n in range(4):
            self.assignment(n)
        for sid in "abc":
            for n in range(3):
                self.submit(f"tr-{sid}", n, 2, root=(sid in "ab" and n == 2))
        for n in range(4):
            self.submit("tr-d", n, 0, strength=(n == 3))
        return self


@pytest.fixture
def world(db_conn):
    return World(db_conn).standard_class()


def analyze(conn, as_of=AS_OF, trigger="manual"):
    return analyze_classroom(PostgresResultsReader(conn), PostgresAnalyticsWriter(conn), CID, as_of, S, trigger)


def latest(conn, view, cols):
    return conn.execute(f"SELECT {cols} FROM {view} WHERE classroom_id = %s ORDER BY 1, 2", (CID,)).fetchall()


def test_adapters_fit_the_targets(db_conn):
    assert isinstance(PostgresResultsReader(db_conn), ResultsReader)
    assert isinstance(PostgresAnalyticsWriter(db_conn), AnalyticsWriter)


def test_reader_returns_verdict_rows(world):
    reader = PostgresResultsReader(world.conn)
    rows = reader.verdict_rows(CID, AS_OF)
    assert len(rows) == 13
    first = next(r for r in rows if r.submission_id == str(world.submissions[("tr-a", 0)]))
    assert (first.student_id, first.classroom_id, first.standard_code, first.verdict, first.support_level) == \
        ("tr-a", CID, GRADE_STD, "growth_area", "developing")
    assert (first.finding_condition, first.overruled, first.confidence, first.submission_type) == \
        ("with a prompt", False, pytest.approx(0.8), "essay")
    assert first.due_at == T0
    strength = next(r for r in rows if r.student_id == "tr-d" and r.notable_strength)
    assert strength.verdict == "meets" and strength.finding_condition is None
    assert [r.student_id for r in reader.verdict_rows(CID, AS_OF, "tr-d")] == ["tr-d"] * 4


def test_reader_overruled_follows_disagree_and_dismiss(world):
    world.respond("tr-a", 0, "dismiss")
    world.respond("tr-a", 1, "disagree")
    world.respond("tr-a", 2, "agree")
    over = {r.submission_id: r.overruled for r in PostgresResultsReader(world.conn).verdict_rows(CID, AS_OF, "tr-a")}
    assert [over[str(world.submissions[("tr-a", n)])] for n in range(3)] == [True, True, False]


def test_reader_links_and_missing_due_date(world):
    reader = PostgresResultsReader(world.conn)
    assert sorted((lk.student_id, lk.grade_concept, lk.root_concept) for lk in reader.link_rows(CID, AS_OF)) == \
        [("tr-a", CONCEPT, "7.W.3.A"), ("tr-b", CONCEPT, "7.W.3.A")]
    assert reader.link_rows(CID, T0 - timedelta(days=1)) == []
    # an assignment with no due date is dated by its submission
    world.assignment(9, due=None)
    world.submit("tr-a", 9, 1, submitted_at=T0 + timedelta(days=100))
    assert len(reader.verdict_rows(CID, AS_OF, "tr-a")) == 3
    late = reader.verdict_rows(CID, T0 + timedelta(days=101), "tr-a")
    assert len(late) == 4 and late[-1].due_at == T0 + timedelta(days=100)


def test_unknown_classroom_starts_no_run(db_conn):
    reader = PostgresResultsReader(db_conn)
    assert not reader.classroom_exists("tr-none")
    before = _n(db_conn, "review_run")
    with pytest.raises(UnknownClassroom):
        analyze_student(reader, PostgresAnalyticsWriter(db_conn), "tr-none", "x", AS_OF, S, "manual")
    assert _n(db_conn, "review_run") == before


def test_rows_land_and_latest_views_show_them(world):
    c = world.conn
    out = analyze(c, trigger="after_review")
    run = c.execute("SELECT run_type, trigger_type, status, scope, finished_at IS NOT NULL, error "
                    "FROM review_run WHERE id = %s", (out.run_id,)).fetchone()
    assert run[:3] == ("trend_analysis", "after_review", "succeeded") and run[4] and run[5] is None
    assert run[3] == {"scope": "classroom", "classroom_id": CID, "student_id": None, "as_of": AS_OF.isoformat()}
    assert latest(c, "concept_status_latest", "student_id, status") == [
        ("tr-a", "needs_attention"), ("tr-b", "needs_attention"), ("tr-c", "needs_attention"), ("tr-d", "secure")]
    row = c.execute("SELECT current_level, basis, points::int[], trend, as_of, concept_code, review_run_id::text "
                    "FROM concept_status_latest WHERE student_id = 'tr-a'").fetchone()
    assert row == (2, "3/3", [2, 2, 2], "unclear", AS_OF, CONCEPT, out.run_id)
    weeks = latest(c, "class_concept_week_latest", "concept_code, week_start, n_level2, n_level0")
    assert len(weeks) == 4 and weeks[-1][2:] == (3, 1)
    assert c.execute("SELECT count(DISTINCT group_id), count(*) FROM reteach_group_latest "
                     "WHERE classroom_id = %s", (CID,)).fetchone() == (1, 2)
    assert c.execute("SELECT root_concept, grade_concept FROM reteach_group_latest WHERE classroom_id = %s",
                     (CID,)).fetchone() == ("7.W.3.A", CONCEPT)
    assert latest(c, "peer_pair_latest", "tutor_id, tutee_id, status") == [
        ("tr-d", "tr-a", "suggested"), ("tr-d", "tr-b", "suggested")]
    assert len(out.statuses) == 4 and len(out.pairs) == 2


def test_second_run_after_a_dismissal_leaves_the_first_run_unchanged(world):
    c = world.conn
    first = analyze(c)
    sql = "SELECT student_id, status, points::int[] FROM concept_status WHERE review_run_id = %s ORDER BY 1"
    before = c.execute(sql, (first.run_id,)).fetchall()
    world.respond("tr-a", 1, "dismiss")
    world.respond("tr-a", 2, "disagree")
    second = analyze(c)
    assert second.run_id != first.run_id
    now = dict(c.execute("SELECT student_id, status FROM concept_status_latest").fetchall())
    assert now["tr-a"] == "emerging" and now["tr-b"] == "needs_attention"
    assert c.execute(sql, (first.run_id,)).fetchall() == before
    assert {s: st for s, st, _ in before}["tr-a"] == "needs_attention"
    pts = c.execute("SELECT points::int[] FROM concept_status WHERE review_run_id = %s AND student_id = 'tr-a'",
                    (second.run_id,)).fetchone()[0]
    assert pts == [2, 0, 0]
    # a left the group, so no group remains, and the latest pairs follow the second run
    assert _n(c, "reteach_group_latest", "classroom_id = %s", (CID,)) == 0
    assert latest(c, "peer_pair_latest", "tutor_id, tutee_id") == [("tr-d", "tr-b"), ("tr-d", "tr-c")]
    assert _n(c, "peer_pair_seen", "review_run_id = %s", (first.run_id,)) == 2  # the first run's rows stay


def test_superseded_and_unreviewed_attempts_are_ignored(world):
    c = world.conn
    world.submit("tr-e", 3, 3, superseded=True)
    world.submit("tr-e", 3, 3, attempt=2, status="failed")
    world.submit("tr-e", 3, 3, attempt=3, status="in_progress")
    assert {row.student_id for row in PostgresResultsReader(c).verdict_rows(CID, AS_OF)} == \
        {"tr-a", "tr-b", "tr-c", "tr-d"}
    out = analyze(c)
    assert "tr-e" not in {st.student_id for st in out.statuses}
    assert _n(c, "concept_status", "student_id = 'tr-e'") == 0
    world.submit("tr-e", 3, 3, attempt=4)  # the current attempt counts
    assert {row.student_id for row in PostgresResultsReader(c).verdict_rows(CID, AS_OF, "tr-e")} == {"tr-e"}


def test_as_of_before_any_work_is_an_empty_succeeded_run(world):
    c = world.conn
    out = analyze(c, as_of=T0 - timedelta(days=1))
    assert out.statuses == [] and out.groups == [] and out.pairs == []
    assert c.execute("SELECT status, finished_at IS NOT NULL FROM review_run WHERE id = %s",
                     (out.run_id,)).fetchone() == ("succeeded", True)
    assert _n(c, "concept_status", "review_run_id = %s", (out.run_id,)) == 0
    assert _n(c, "class_concept_week", "review_run_id = %s", (out.run_id,)) == 0


def test_student_scope_writes_concept_status_only(world):
    c = world.conn
    out = analyze_student(PostgresResultsReader(c), PostgresAnalyticsWriter(c), CID, "tr-a", AS_OF, S, "manual")
    assert [(st.student_id, st.status) for st in out.statuses] == [("tr-a", "needs_attention")]
    assert _n(c, "concept_status", "review_run_id = %s", (out.run_id,)) == 1
    for table in ("class_concept_week", "reteach_group", "peer_pair_seen"):
        assert _n(c, table, "review_run_id = %s", (out.run_id,)) == 0
    scope = c.execute("SELECT scope FROM review_run WHERE id = %s", (out.run_id,)).fetchone()[0]
    assert scope["scope"] == "student" and scope["student_id"] == "tr-a" and scope["classroom_id"] == CID
    # a student-scope run is never the classroom's latest run (groups and pairs come from classroom runs)
    assert _n(c, "classroom_run_latest", "classroom_id = %s", (CID,)) == 0


def test_a_decided_pair_keeps_its_decision_and_is_listed_again(world):
    c = world.conn
    analyze(c)
    c.execute("UPDATE peer_pair_suggestion SET status = 'approved', decided_by = 'tr-t', decided_at = now() "
              "WHERE tutee_id = 'tr-a'")
    second = analyze(c)
    assert _n(c, "peer_pair_suggestion", "classroom_id = %s", (CID,)) == 2  # no duplicate row
    assert _n(c, "peer_pair_seen", "review_run_id = %s", (second.run_id,)) == 2
    assert latest(c, "peer_pair_latest", "tutee_id, status, decided_by") == [
        ("tr-a", "approved", "tr-t"), ("tr-b", "suggested", None)]


def test_a_failed_save_rolls_back_and_marks_the_run_failed(world):
    c = world.conn

    class Bad(PostgresAnalyticsWriter):
        def save(self, run, statuses, weeks, groups, pairs):
            super().save(run, statuses, weeks, groups, [PairSuggestion(CONCEPT, "tr-d", "tr-d")])  # tutor == tutee

    with pytest.raises(Exception) as err:
        analyze_classroom(PostgresResultsReader(c), Bad(c), CID, AS_OF, S, "manual")
    row = c.execute("SELECT status, error, finished_at IS NOT NULL, id::text FROM review_run "
                    "WHERE run_type = 'trend_analysis' AND scope->>'classroom_id' = %s", (CID,)).fetchone()
    assert row[:3] == ("failed", type(err.value).__name__, True)
    assert _n(c, "concept_status", "review_run_id = %s", (row[3],)) == 0  # one transaction: nothing partial
    assert _n(c, "concept_status_latest", "classroom_id = %s", (CID,)) == 0


def test_reset_reviews_removes_the_analytics_rows(world):
    c = world.conn
    analyze(c)
    analyze_student(PostgresResultsReader(c), PostgresAnalyticsWriter(c), CID, "tr-a", AS_OF, S, "manual")
    c.execute("INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name) "
              "VALUES ('tr-c2', 'tr-c2', 'tr-t', 'ela', 9, 'Other')")
    other = PostgresAnalyticsWriter(c)
    run2 = AnalysisRun(uuid4(), "classroom", "tr-c2", None, AS_OF, "manual")
    other.start(run2)
    other.save(run2, [], [], [], [])
    assert _n(c, "peer_pair_suggestion", "classroom_id = %s", (CID,)) == 2
    counts = reset_reviews(c, CID)
    assert counts["concept_status"] == 5 and counts["peer_pair_suggestion"] == 2
    for table in ("concept_status", "class_concept_week", "reteach_group", "peer_pair_suggestion"):
        assert _n(c, table, "classroom_id = %s", (CID,)) == 0
    assert _n(c, "reteach_group_member", "student_id LIKE 'tr-%%'") == 0
    assert _n(c, "peer_pair_seen", "review_run_id IN (SELECT id FROM review_run "
              "WHERE scope->>'classroom_id' = %s)", (CID,)) == 0
    assert _n(c, "review_run", "run_type = 'trend_analysis' AND scope->>'classroom_id' = %s", (CID,)) == 0
    assert _n(c, "review_run", "id = %s", (run2.run_id,)) == 1  # another classroom's run is kept
    assert _n(c, "submission", "student_id LIKE 'tr-%%'") == 0


def test_reader_maps_lms_ids_to_sass_ids(world):
    world.student("tr-a")
    reader = PostgresResultsReader(world.conn)
    assert reader.classroom_for_course(CID) == CID and reader.classroom_for_course("nope") is None
    assert reader.student_for_user("tr-a") == "tr-a" and reader.student_for_user("nope") is None
