from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from agentic.results.seed import seed_course

DATA = Path(__file__).parent.parent / "data" / "coursedir"
CONTROL, INTERVENTION = "classroom_0.90-control", "classroom_0.90-intervention"


class RecordingConn:
    """Minimal fake: records (sql, params) and supports transaction() as a context manager."""

    def __init__(self):
        self.calls = []
        self.in_tx = 0

    def transaction(self):
        conn = self

        class _Tx:
            def __enter__(self):
                conn.in_tx += 1

            def __exit__(self, *exc):
                conn.in_tx -= 1

        return _Tx()

    def execute(self, sql, params=None):
        assert self.in_tx, "writes must happen inside a transaction"
        self.calls.append((" ".join(sql.split()), tuple(params or ())))

    def rows(self, table):
        return [p for s, p in self.calls if s.startswith(f"INSERT INTO {table} ")]


def test_seed_course_writes_both_twins_unit():
    conn = RecordingConn()
    counts = seed_course(conn, DATA, "0.90-control")
    assert counts == {"classrooms": 2, "students": 4, "assignments": 7}
    classrooms = {r[0]: r for r in conn.rows("classroom")}
    assert set(classrooms) == {CONTROL, INTERVENTION}
    assert classrooms[CONTROL][1] == CONTROL  # lms_course_id
    assert 9 in classrooms[CONTROL]
    enrolled = {(r[0], r[1]): r[2] for r in conn.rows("enrollment")}
    assert set(enrolled) == {(CONTROL, "a1a1a1a1a1a1a1a1"), (CONTROL, "b2b2b2b2b2b2b2b2"),
                             (INTERVENTION, "c3c3c3c3c3c3c3c3"), (INTERVENTION, "d4d4d4d4d4d4d4d4")}
    assert set(enrolled.values()) == {date(2027, 1, 11)}
    assert {r[0] for r in conn.rows("student")} == {h for _, h in enrolled}
    asg = {r[0]: r for r in conn.rows("assignment")}
    assert set(asg) == {f"ela9v2-a0{n}@0.90-{arm}" for n in (1, 2, 3) for arm in ("control", "intervention")} \
        | {"ela9v2-i01@0.90-intervention"}
    assert "ela9v2-i01@0.90-control" not in asg
    a2 = asg["ela9v2-a02@0.90-control"]
    assert a2[1] == a2[0]  # lms_assignment_id
    assert a2[2] == CONTROL
    assert isinstance(a2[4], datetime) and a2[4].tzinfo is not None
    assert a2[4].astimezone(timezone.utc).hour in (5, 6)  # 23:59 Chicago is the next UTC day
    assert asg["ela9v2-i01@0.90-intervention"][2] == INTERVENTION
    from agentic.coursedata import CourseDir
    want = {"essay": "essay", "paragraph": "essay", "short_answer": "quiz", "multiple_choice": "quiz"}
    for a in CourseDir(DATA, "0.90-intervention").assignments():
        row = asg[f"{a.id}@0.90-intervention"]
        assert row[3] == a.title and row[5] == want[a.type]


def test_seed_course_submission_type_mapping_unit(tmp_path):
    from agentic.results.seed import _SUBMISSION_TYPE
    assert _SUBMISSION_TYPE == {"essay": "essay", "paragraph": "essay",
                                "short_answer": "quiz", "multiple_choice": "quiz"}


@pytest.mark.db
def test_seed_course_db_idempotent(db_conn):
    first = seed_course(db_conn, DATA, "0.90-control")
    assert first == {"classrooms": 2, "students": 4, "assignments": 7}
    assert seed_course(db_conn, DATA, "0.90-control") == first
    rows = db_conn.execute("SELECT classroom_id FROM assignment WHERE lms_assignment_id LIKE %s",
                           ("ela9v2-i01@0.90-%",)).fetchall()
    assert [r[0] for r in rows] == [INTERVENTION]
