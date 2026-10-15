import pytest
from fakes import essay_row, label_row, std_row, write_essays, write_labels, xlsx_fixture

from agentic.llm.fake import FakeEmbedder
from agentic.models import (Finding, Observation, ReviewResult, StandardRef, SubmissionRef,
                            Verification)
from agentic.results.base import ResultsWriter
from agentic.results.postgres_writer import PostgresResultsWriter
from agentic.results.seed import seed_demo
from agentic.standards.loader import load, normalize_rows, read_xlsx

pytestmark = pytest.mark.db

H = "a" * 64
SECRET = "Secret essay sentence alpha."


@pytest.fixture
def seeded(db_conn, tmp_path):
    write_labels(tmp_path, "1.01", [label_row(student_hash=H, grade=9),
                                    label_row(student_hash="b" * 64, grade=9)])
    write_essays(tmp_path, "1.01", [
        essay_row(student_hash=H, paragraphs=[SECRET, "Beta."], course_id="classroom_1.01",
                  assignment_id="asg-classroom_1.01-essay1"),
        essay_row(student_hash="b" * 64, paragraphs=["Other."], course_id="classroom_1.01",
                  assignment_id="asg-classroom_1.01-essay1", submission_id="sub-2")])
    seed_demo(db_conn, tmp_path, "1.01")
    rows = normalize_rows(read_xlsx(xlsx_fixture(tmp_path, [std_row("t.1"), std_row("t.2")])))
    load(db_conn, rows, FakeEmbedder())
    return db_conn, tmp_path


def _result(codes=("t.1", "t.2"), sub="sub-1", attempt=1):
    ref = SubmissionRef(course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1",
                        user_id=H, submission_id=sub, attempt=attempt)
    obs = [Observation(standard_code=codes[0], paragraph=1, reason="Ideas lack supporting detail"),
           Observation(standard_code=codes[0], paragraph=2, reason="Needs more evidence"),
           Observation(standard_code=codes[1], paragraph=2, reason="Transitions are missing")]
    finds = [Finding(standard_code=codes[0], concept="C", description="D", paragraphs=[1, 2],
                     reasons=["a", "b"], observation_count=2, summary="Two paragraphs need work"),
             Finding(standard_code=codes[1], concept="C", description="D", paragraphs=[2],
                     reasons=["a"], observation_count=1, summary="One paragraph needs work")]
    return ReviewResult(submission_ref=ref, course_grade=9, strategy="full", standards=[],
                        observations=obs, findings=finds, met_standards=[], dropped=[],
                        verification=Verification(status="disabled"))


def _n(conn, table, where="true"):
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()[0]


def test_seed_demo_idempotent(db_conn, tmp_path):
    write_labels(tmp_path, "1.01", [label_row(student_hash=H), label_row(student_hash="b" * 64)])
    write_essays(tmp_path, "1.01", [essay_row(student_hash=H, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1"),
                                    essay_row(student_hash="b" * 64, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1")])
    assert seed_demo(db_conn, tmp_path, "1.01") == 2
    assert seed_demo(db_conn, tmp_path, "1.01") == 2
    assert _n(db_conn, "student", f"id IN ('{H}', '{'b' * 64}')") == 2
    assert _n(db_conn, "classroom", "id = 'classroom_1.01' AND grade_level = 9 AND subject = 'ela'") == 1
    assert _n(db_conn, "assignment", "id = 'asg-classroom_1.01-essay1'") == 1
    assert _n(db_conn, "teacher", "id = 'demo-teacher'") == 1


def test_writer_is_a_results_writer(seeded):
    assert isinstance(PostgresResultsWriter(seeded[0]), ResultsWriter)


def test_save_writes_expected_rows(seeded):
    conn, _ = seeded
    out = PostgresResultsWriter(conn).save(_result())
    assert out.status == "written"
    run = conn.execute("SELECT status, submission_count, completed_count, finished_at IS NOT NULL, error "
                       "FROM review_run WHERE id = %s", (out.review_run_id,)).fetchone()
    assert run == ("succeeded", 1, 1, True, None)
    sub = conn.execute("SELECT id, review_status, review_run_id::text, reviewed_at IS NOT NULL, "
                       "chunker_version FROM submission WHERE lms_submission_id = 'sub-1'").fetchone()
    assert sub[1:] == ("succeeded", out.review_run_id, True, "mvp-whole-essay-1")
    sid = sub[0]
    obs = conn.execute("SELECT chunk_index, level, reason FROM observation WHERE submission_id = %s "
                       "ORDER BY chunk_index, reason", (sid,)).fetchall()
    assert [(o[0], o[1]) for o in obs] == [(0, "paragraph"), (1, "paragraph"), (1, "paragraph")]
    fs = conn.execute("SELECT summary, observation_count, "
                      "(SELECT count(*) FROM finding_observation fo WHERE fo.finding_id = f.id), "
                      "(SELECT count(*) FROM finding_standard fs WHERE fs.finding_id = f.id) "
                      "FROM finding f WHERE submission_id = %s ORDER BY observation_count DESC", (sid,)).fetchall()
    assert [(f[1], f[2], f[3]) for f in fs] == [(2, 2, 1), (1, 1, 1)]
    dump = " ".join(str(v) for t in ("review_run", "submission", "observation", "finding")
                    for row in conn.execute(f"SELECT * FROM {t}").fetchall() for v in row)
    assert SECRET not in dump


def test_save_duplicate_attempt(seeded):
    conn, _ = seeded
    w = PostgresResultsWriter(conn)
    first = w.save(_result())
    before = {t: _n(conn, t) for t in ("review_run", "submission", "observation", "finding")}
    second = w.save(_result())
    assert second.status == "duplicate"
    assert second.review_run_id == first.review_run_id
    assert {t: _n(conn, t) for t in before} == before


def test_failed_save_leaves_no_observations(seeded):
    conn, _ = seeded
    runs_before = _n(conn, "review_run")
    with pytest.raises(LookupError):
        PostgresResultsWriter(conn).save(_result(codes=("t.1", "no.such.code")))
    assert _n(conn, "review_run") == runs_before + 1
    run = conn.execute("SELECT status, error FROM review_run ORDER BY created_at DESC LIMIT 1").fetchone()
    assert run == ("failed", "LookupError")
    assert _n(conn, "observation", "submission_id IN (SELECT id FROM submission WHERE lms_submission_id = 'sub-1')") == 0
    assert _n(conn, "submission", "lms_submission_id = 'sub-1' AND review_status = 'succeeded'") == 0


def test_retry_after_failure_succeeds(seeded):
    conn, _ = seeded
    w = PostgresResultsWriter(conn)
    with pytest.raises(LookupError):
        w.save(_result(codes=("t.1", "no.such.code")))
    out = w.save(_result())
    assert out.status == "written"
    assert _n(conn, "submission", "lms_submission_id = 'sub-1' AND review_status = 'succeeded'") == 1


def test_save_records_trigger(seeded):
    conn, _ = seeded
    w = PostgresResultsWriter(conn)
    out = w.save(_result(), trigger="on_grade")
    trigger = conn.execute("SELECT trigger_type FROM review_run WHERE id = %s", (out.review_run_id,)).fetchone()
    assert trigger == ("on_grade",)


def test_is_reviewed(seeded):
    conn, _ = seeded
    w = PostgresResultsWriter(conn)
    assert not w.is_reviewed("sub-1", 1)
    w.save(_result())
    assert w.is_reviewed("sub-1", 1) and not w.is_reviewed("sub-1", 2)


def test_reset_reviews_clears_one_classroom_only(seeded):
    from agentic.results.reset import reset_reviews

    conn, _ = seeded
    out = PostgresResultsWriter(conn).save(_result())
    sid = conn.execute("SELECT id FROM submission WHERE lms_submission_id = 'sub-1'").fetchone()[0]
    fid = conn.execute("SELECT id FROM finding WHERE submission_id = %s LIMIT 1", (sid,)).fetchone()[0]
    conn.execute("INSERT INTO teacher_response (finding_id, teacher_id, stance) VALUES (%s, 'demo-teacher', 'agree')",
                 (fid,))
    assert reset_reviews(conn, "classroom_no_such")["submission"] == 0
    assert _n(conn, "submission", "lms_submission_id = 'sub-1'") == 1

    counts = reset_reviews(conn, "classroom_1.01")
    assert counts["finding"] >= 2 and counts["observation"] >= 3 and counts["teacher_response"] >= 1
    assert _n(conn, "submission", "lms_submission_id = 'sub-1'") == 0
    assert _n(conn, "finding", f"submission_id = '{sid}'") == 0
    assert _n(conn, "review_run", f"id = '{out.review_run_id}'") == 0
    assert _n(conn, "standard", "code IN ('t.1', 't.2')") == 2  # kept
    assert _n(conn, "student", f"id = '{H}'") == 1 and _n(conn, "classroom", "id = 'classroom_1.01'") == 1
    assert not PostgresResultsWriter(conn).is_reviewed("sub-1", 1)  # can be reviewed again


def _lms_named_class(conn):
    """A classroom whose SASS keys differ from its LMS IDs (as a backend import might create)."""
    conn.execute("INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name) "
                 "VALUES ('sass-class-9', 'lms-course-9', 'demo-teacher', 'ela', 9, 'Class 9')")
    conn.execute("INSERT INTO assignment (id, lms_assignment_id, classroom_id, title, submission_type) "
                 "VALUES ('sass-asg-9', 'lms-asg-9', 'sass-class-9', 'Essay', 'essay')")
    conn.execute("INSERT INTO student (id, lms_user_id) VALUES ('sass-stu-9', 'lms-user-9')")


def _lms_result(user="lms-user-9"):
    r = _result(sub="lms-sub-9")
    ref = r.submission_ref.model_copy(update={"course_id": "lms-course-9", "assignment_id": "lms-asg-9",
                                              "user_id": user})
    return r.model_copy(update={"submission_ref": ref})


def test_save_maps_lms_ids_to_sass_rows(seeded):
    conn, _ = seeded
    _lms_named_class(conn)
    out = PostgresResultsWriter(conn).save(_lms_result())
    assert out.status == "written"
    row = conn.execute("SELECT assignment_id, student_id FROM submission WHERE lms_submission_id = 'lms-sub-9'"
                       ).fetchone()
    assert row == ("sass-asg-9", "sass-stu-9")


def test_save_unseeded_student_fails_cleanly(seeded):
    conn, _ = seeded
    _lms_named_class(conn)
    with pytest.raises(LookupError) as ei:
        PostgresResultsWriter(conn).save(_lms_result(user="lms-user-unknown"))
    assert "lms-user-unknown" not in str(ei.value)
    assert _n(conn, "submission", "lms_submission_id = 'lms-sub-9'") == 0
    assert _n(conn, "review_run", "status = 'failed' AND error = 'LookupError'") >= 1


def test_reset_by_lms_course_id(seeded):
    from agentic.results.reset import reset_reviews

    conn, _ = seeded
    _lms_named_class(conn)
    PostgresResultsWriter(conn).save(_lms_result())
    assert reset_reviews(conn, "sass-class-9")["submission"] == 0  # the SASS key is not the LMS ID
    assert reset_reviews(conn, "lms-course-9")["submission"] == 1
