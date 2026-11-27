"""GET /dashboard and GET /notifications.

Expected numbers are counted straight from Justin's tables, not from the endpoint's own SQL.
Tests that change data (a failed review, a decided pair) put it back afterwards."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.config import settings
from app.main import app
from app.routers.dashboard import _plural

client = TestClient(app)
CLASS = "classroom_2.00-control"


def _rows(sql, params=()):
    try:
        return db.query(sql, params)
    except db.DatabaseUnavailable:
        return []


needs_data = pytest.mark.skipif(
    not _rows("SELECT 1 FROM concept_status_latest WHERE classroom_id = %s LIMIT 1", (CLASS,)),
    reason="run `python -m agentic_lab fill-demo` first")


def _expected(cid):
    attention = _rows("""SELECT DISTINCT student_id FROM concept_status_latest
                         WHERE classroom_id = %s AND status = 'needs_attention' ORDER BY student_id""", (cid,))
    pending = _rows("SELECT COUNT(*) AS n FROM peer_pair_latest WHERE classroom_id = %s AND status = 'suggested'", (cid,))
    return [r["student_id"] for r in attention], pending[0]["n"]


def test_plural():
    assert _plural(1, "student needs", "students need") == "1 student needs"
    assert _plural(3, "student needs", "students need") == "3 students need"


@needs_data
def test_dashboard_numbers_match_the_analysis():
    body = client.get("/dashboard").json()
    card = next(c for c in body["classrooms"] if c["classroom_id"] == CLASS)
    attention, pending = _expected(CLASS)
    assert card["students_needing_attention"] == len(attention) > 0
    assert card["pending_pairs"] == pending
    assert card["student_count"] == 18
    assert card["last_reviewed_at"] and card["analysis_as_of"]
    t = body["totals"]
    for key in ("students", "students_needing_attention", "pending_pairs", "failed_reviews"):
        assert t[key] == sum(c[key if key != "students" else "student_count"] for c in body["classrooms"])
    assert t["classrooms"] == len(body["classrooms"])
    text = str(body).lower()
    assert "percent" not in text and "%" not in text


@needs_data
def test_notifications_agree_with_the_dashboard():
    cards = {c["classroom_id"]: c for c in client.get("/dashboard").json()["classrooms"]}
    notes = client.get("/notifications").json()
    attention, pending = _expected(CLASS)
    mine = {n["kind"]: n for n in notes if n["classroom_id"] == CLASS}
    assert mine["needs_attention"]["count"] == cards[CLASS]["students_needing_attention"]
    assert mine["needs_attention"]["student_ids"] == attention
    assert mine["needs_attention"]["message"] == f"{len(attention)} students need attention in English 9, Period 1."
    assert mine["pending_pairs"]["count"] == pending
    assert mine["needs_attention"]["id"] == f"needs_attention:{CLASS}"
    # a class with nothing to report gets no notification
    quiet = [cid for cid, c in cards.items()
             if not (c["students_needing_attention"] or c["pending_pairs"] or c["failed_reviews"])]
    assert not [n for n in notes if n["classroom_id"] in quiet]
    kinds = [n["kind"] for n in notes]
    assert kinds == sorted(kinds, key=["failed_reviews", "needs_attention", "pending_pairs"].index)


@pytest.fixture
def failed_review():
    row = _rows("""SELECT s.id, s.reviewed_at FROM submission s JOIN assignment a ON a.id = s.assignment_id
                   WHERE a.classroom_id = %s AND s.review_status = 'succeeded' ORDER BY s.id LIMIT 1""", (CLASS,))[0]
    # The database requires reviewed_at to be empty unless the review succeeded.
    db.execute("UPDATE submission SET review_status = 'failed', reviewed_at = NULL WHERE id = %s", (row["id"],))
    yield
    db.execute("UPDATE submission SET review_status = 'succeeded', reviewed_at = %s WHERE id = %s",
               (row["reviewed_at"], row["id"]))


@needs_data
def test_a_failed_review_shows_first(failed_review):
    card = next(c for c in client.get("/dashboard").json()["classrooms"] if c["classroom_id"] == CLASS)
    assert card["failed_reviews"] == 1
    first = client.get("/notifications").json()[0]
    assert first["kind"] == "failed_reviews" and first["classroom_id"] == CLASS
    assert first["message"] == "1 essay could not be reviewed in English 9, Period 1."


@pytest.fixture
def decided_pair():
    pid = _rows("""SELECT pair_id FROM peer_pair_latest WHERE classroom_id = %s AND status = 'suggested'
                   ORDER BY pair_id LIMIT 1""", (CLASS,))[0]["pair_id"]
    yield str(pid)
    db.execute("UPDATE peer_pair_suggestion SET status = 'suggested', decided_by = NULL, decided_at = NULL "
               "WHERE id = %s", (pid,))


@needs_data
def test_deciding_a_pair_lowers_the_count_everywhere(decided_pair):
    def counts():
        card = next(c for c in client.get("/dashboard").json()["classrooms"] if c["classroom_id"] == CLASS)
        note = next(n for n in client.get("/notifications").json()
                    if n["classroom_id"] == CLASS and n["kind"] == "pending_pairs")
        return card["pending_pairs"], note["count"]
    before = counts()
    assert client.patch(f"/peer-pairs/{decided_pair}", json={"status": "approved"}).status_code == 200
    assert counts() == (before[0] - 1, before[1] - 1)


# ---- with real logins -------------------------------------------------------------------

@pytest.fixture
def other_teacher():
    settings.jwt_secret = settings.jwt_secret or "test-secret-" + "x" * 40
    tag = uuid.uuid4().hex[:8]
    tid = f"test-teacher-{tag}"
    db.execute("INSERT INTO teacher (id, name) VALUES (%s, 'Other Teacher')", (tid,))
    uid = db.execute("""INSERT INTO app_user (email, password_hash, role, teacher_id, display_name)
                        VALUES (%s, 'unused', 'teacher', %s, 'Ms. Other') RETURNING id""",
                     (f"other-{tag}@sass.test", tid))[0]["id"]
    yield {"Authorization": f"Bearer {auth.create_token(uid, 'teacher')}"}
    db.execute("DELETE FROM app_user WHERE id = %s", (uid,))
    db.execute("DELETE FROM teacher WHERE id = %s", (tid,))


@needs_data
@pytest.mark.real_auth
def test_a_teacher_sees_only_their_own_classes(other_teacher):
    body = client.get("/dashboard", headers=other_teacher).json()
    assert body["classrooms"] == [] and body["totals"]["students_needing_attention"] == 0
    assert body["user"] == {"display_name": "Ms. Other", "role": "teacher"}
    assert client.get("/notifications", headers=other_teacher).json() == []


@pytest.mark.real_auth
def test_no_login_is_401():
    assert client.get("/dashboard").status_code == 401
    assert client.get("/notifications").status_code == 401
