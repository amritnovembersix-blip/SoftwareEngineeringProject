"""findings_by_area against local Postgres: seeded inside one transaction that is always rolled back."""
import uuid
from datetime import datetime, timezone

import pytest

from agentic_lab.sim.findings_query import findings_by_area

pytestmark = pytest.mark.db

GRAMMAR, REVISE = "3–5 · L · Grammar", "9–10 · W · Revise and Edit"
PUNCT = "3–5 · L · Punctuation, Capitalization, Spelling"


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    try:
        from agentic.wiring import connect

        from agentic_lab.config import lab_settings

        c = connect(lab_settings(), autocommit=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres unavailable ({type(exc).__name__})")
    try:
        with c.transaction(force_rollback=True):
            yield c
    finally:
        c.close()


def _seed(conn) -> dict:
    tag = uuid.uuid4().hex[:8]
    course = f"classroom_simtest-{tag}"
    std = dict(conn.execute("SELECT code, id FROM standard WHERE code = ANY(%s)",
                            (["3.L.1.A.a", "4.L.1.A.a", "3.L.1.B.b", "9-10.W.3.A.a"],)).fetchall())
    if len(std) < 4:
        pytest.skip("standards catalog not loaded")
    x = conn.execute
    x("INSERT INTO teacher (id, name) VALUES (%s, 'T')", (f"t-{tag}",))
    x("INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name) "
      "VALUES (%s, %s, %s, 'ela', 9, 'C')", (f"c-{tag}", course, f"t-{tag}"))
    # A second classroom whose findings must not leak into the first.
    x("INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name) "
      "VALUES (%s, %s, %s, 'ela', 9, 'Other')", (f"c2-{tag}", f"{course}-other", f"t-{tag}"))
    students = {name: f"{name}{tag}" for name in ("alice", "bob")}
    for sid in students.values():
        x("INSERT INTO student (id, lms_user_id) VALUES (%s, %s)", (f"s-{sid}", sid))
        x("INSERT INTO enrollment (classroom_id, student_id, start_date) VALUES (%s, %s, '2027-01-11')",
          (f"c-{tag}", f"s-{sid}"))
    due = {"a1": _utc(2027, 1, 16, 5, 59), "a2": _utc(2027, 1, 23, 5, 59), "other": _utc(2027, 1, 16, 5, 59)}
    for a, d in due.items():
        x("INSERT INTO assignment (id, lms_assignment_id, classroom_id, title, due_at, submission_type) "
          "VALUES (%s, %s, %s, %s, %s, 'essay')",
          (f"{a}-{tag}", f"{a}@{tag}", f"c2-{tag}" if a == "other" else f"c-{tag}", a, d))
    run = x("INSERT INTO review_run (run_type, trigger_type) VALUES ('submission_review', 'on_grade') "
            "RETURNING id").fetchone()[0]

    def submission(student, assignment, status="succeeded"):
        return x("INSERT INTO submission (lms_submission_id, attempt, assignment_id, student_id, submitted_at, "
                 "review_run_id, review_status, reviewed_at) VALUES (%s, 1, %s, %s, now(), %s, %s, %s) "
                 "RETURNING id",
                 (uuid.uuid4().hex, f"{assignment}-{tag}", f"s-{students[student]}", run, status,
                  datetime.now(timezone.utc) if status == "succeeded" else None)).fetchone()[0]

    def finding(sub, level, codes):
        fid = x("INSERT INTO finding (submission_id, summary, observation_count, support_level) "
                "VALUES (%s, 'Synthetic test finding.', 1, %s) RETURNING id", (sub, level)).fetchone()[0]
        for c in codes:
            x("INSERT INTO finding_standard (finding_id, standard_id) VALUES (%s, %s)", (fid, std[c]))

    def strength(sub, code):
        x("INSERT INTO verdict (submission_id, standard_id, verdict, notable_strength, strength_note) "
          "VALUES (%s, %s, 'meets', true, 'Synthetic strength note.')", (sub, std[code]))

    alice1 = submission("alice", "a1")
    finding(alice1, "approaching", ["3.L.1.A.a", "4.L.1.A.a"])  # one finding, two standards in one area
    finding(alice1, "beginning", ["3.L.1.A.a"])
    finding(alice1, None, ["9-10.W.3.A.a"])
    strength(alice1, "3.L.1.B.b")  # a strength with no finding in its area
    strength(alice1, "4.L.1.A.a")
    x("INSERT INTO verdict (submission_id, standard_id, verdict, support_level) "
      "VALUES (%s, %s, 'growth_area', 'developing')", (alice1, std["3.L.1.A.a"]))  # not a strength
    alice2 = submission("alice", "a2")  # due after week 1
    finding(alice2, "developing", ["3.L.1.A.a"])
    bob1 = submission("bob", "a1", status="in_progress")  # not reviewed yet
    finding(bob1, "beginning", ["3.L.1.A.a"])
    bob_other = submission("bob", "other")  # another classroom
    finding(bob_other, "beginning", ["3.L.1.A.a"])
    return {"course": course, "alice": students["alice"], "due": due}


def _by_key(cells):
    return {(c.student_hash, c.area, c.due_at): c for c in cells}


def test_counts_levels_and_strengths_per_area(conn):
    seed = _seed(conn)
    cells = _by_key(findings_by_area(conn, seed["course"], _utc(2027, 2, 1)))
    alice, a1, a2 = seed["alice"], seed["due"]["a1"], seed["due"]["a2"]
    assert set(cells) == {(alice, GRAMMAR, a1), (alice, REVISE, a1), (alice, PUNCT, a1), (alice, GRAMMAR, a2)}
    g = cells[(alice, GRAMMAR, a1)]
    assert (g.count, g.level, g.strengths) == (2, "beginning", 1)
    r = cells[(alice, REVISE, a1)]
    assert (r.count, r.level, r.strengths) == (1, None, 0)
    p = cells[(alice, PUNCT, a1)]
    assert (p.count, p.level, p.strengths) == (0, None, 1)
    later = cells[(alice, GRAMMAR, a2)]
    assert (later.count, later.level) == (1, "developing")


def test_through_cuts_off_later_assignments(conn):
    seed = _seed(conn)
    cells = findings_by_area(conn, seed["course"], _utc(2027, 1, 18, 5, 59, 59))
    assert {c.due_at for c in cells} == {seed["due"]["a1"]}
    assert all(c.student_hash == seed["alice"] for c in cells)  # bob's in_progress submission is excluded


def test_unknown_course_is_empty(conn):
    assert findings_by_area(conn, "classroom_no-such-course", _utc(2030, 1, 1)) == []
