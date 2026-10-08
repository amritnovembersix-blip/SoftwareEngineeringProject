import pytest
from fakes import std_row, xlsx_fixture

from agentic.llm.fake import FakeEmbedder
from agentic.standards.loader import load, normalize_rows, read_xlsx

pytestmark = pytest.mark.db


def _rows(tmp_path, *specs):
    return normalize_rows(read_xlsx(xlsx_fixture(tmp_path, list(specs))))


def _count(conn, where="true"):
    return conn.execute(f"SELECT count(*) FROM standard WHERE {where}").fetchone()[0]


@pytest.fixture
def clean(db_conn):
    # The dev DB may hold other standards; remove them inside this (rolled-back) transaction
    # so counts are exact.
    db_conn.execute("DELETE FROM observation")
    db_conn.execute("DELETE FROM finding_standard")
    db_conn.execute("DELETE FROM standard")
    return db_conn


def test_load_upserts_embeds_and_is_rerunnable(clean, tmp_path):
    rows = _rows(tmp_path, std_row("t.1"), std_row("t.2", description="Other."))
    emb = FakeEmbedder()
    first = load(clean, rows, emb)
    assert (first.upserted, first.deleted, first.embedded) == (2, 0, 2)
    assert _count(clean, "embedding IS NOT NULL") == 2
    second = load(clean, rows, emb)
    assert (second.upserted, second.deleted, second.embedded) == (2, 0, 0)
    changed = _rows(tmp_path, std_row("t.1", description="Changed."), std_row("t.2", description="Other."))
    assert load(clean, changed, emb).embedded == 1


def test_load_deletes_removed_codes(clean, tmp_path):
    emb = FakeEmbedder()
    load(clean, _rows(tmp_path, std_row("t.1"), std_row("t.2")), emb)
    report = load(clean, _rows(tmp_path, std_row("t.1")), emb)
    assert report.deleted == 1
    assert _count(clean) == 1


def test_load_refuses_delete_when_referenced(clean, tmp_path):
    emb = FakeEmbedder()
    load(clean, _rows(tmp_path, std_row("t.1"), std_row("t.2")), emb)
    c = clean
    c.execute("INSERT INTO teacher (id, name) VALUES ('t5', 'T')")
    c.execute("INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name) "
              "VALUES ('c5', 'lms-c5', 't5', 'ela', 9, 'C')")
    c.execute("INSERT INTO student (id, lms_user_id) VALUES ('s5', 'lms-u5')")
    c.execute("INSERT INTO assignment (id, lms_assignment_id, classroom_id, title, submission_type) "
              "VALUES ('a5', 'lms-a5', 'c5', 'A', 'essay')")
    run_id = c.execute("INSERT INTO review_run (run_type, trigger_type) "
                       "VALUES ('submission_review', 'manual') RETURNING id").fetchone()[0]
    sub_id = c.execute("INSERT INTO submission (lms_submission_id, attempt, assignment_id, student_id, "
                       "submitted_at, review_run_id) VALUES ('l5', 1, 'a5', 's5', now(), %s) RETURNING id",
                       (run_id,)).fetchone()[0]
    c.execute("INSERT INTO observation (submission_id, chunk_index, level, standard_id, reason) "
              "SELECT %s, 0, 'paragraph', id, 'short reason' FROM standard WHERE code = 't.2'", (sub_id,))
    with pytest.raises(ValueError, match=r"t\.2"):
        load(c, _rows(tmp_path, std_row("t.1")), emb)
    assert _count(c) == 2
