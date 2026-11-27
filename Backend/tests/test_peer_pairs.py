"""FR7: GET /classrooms/{id}/peer-pairs and PATCH /peer-pairs/{id}.

Expected values come from Justin's tables (peer_pair_latest, peer_pair_suggestion). Every test that
decides a pair puts it back to "suggested" afterwards, so the demo data is left as it was."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.config import settings
from app.main import app
from tests.conftest import TEST_ADMIN

client = TestClient(app)
CLASS = "classroom_2.00-control"


def _rows(sql, params=()):
    try:
        return db.query(sql, params)
    except db.DatabaseUnavailable:
        return []


HAS_PAIRS = bool(_rows("SELECT 1 FROM peer_pair_latest WHERE classroom_id = %s LIMIT 1", (CLASS,)))
pytestmark = pytest.mark.skipif(not HAS_PAIRS, reason="run `python -m agentic_lab fill-demo` first")


@pytest.fixture
def pair():
    """A suggested pair from the demo class, restored to 'suggested' after the test."""
    pid = str(_rows("""SELECT pair_id FROM peer_pair_latest WHERE classroom_id = %s AND status = 'suggested'
                       ORDER BY pair_id LIMIT 1""", (CLASS,))[0]["pair_id"])
    yield pid
    db.execute("UPDATE peer_pair_suggestion SET status = 'suggested', decided_by = NULL, decided_at = NULL "
               "WHERE id = %s", (pid,))


def _saved(pid):
    return db.query("SELECT status, decided_by, decided_at FROM peer_pair_suggestion WHERE id = %s", (pid,))[0]


# ---- the list ---------------------------------------------------------------------------

def test_list_matches_the_latest_analysis():
    body = client.get(f"/classrooms/{CLASS}/peer-pairs").json()
    expected = _rows("SELECT pair_id, status FROM peer_pair_latest WHERE classroom_id = %s", (CLASS,))
    assert {p["pair_id"] for p in body["pairs"]} == {str(r["pair_id"]) for r in expected}
    assert body["pending"] == sum(r["status"] == "suggested" for r in expected)


def test_each_pair_says_why_and_has_no_scores():
    body = client.get(f"/classrooms/{CLASS}/peer-pairs").json()
    for p in body["pairs"]:
        assert p["tutor"]["student_id"] != p["tutee"]["student_id"]
        assert p["skill_code"] and p["strand"] and p["status_label"]
        assert p["tutor"]["status_label"] and p["tutee"]["status_label"]
    text = str(body).lower()
    assert "percent" not in text and "score" not in text and "%" not in text


def test_waiting_pairs_come_first(pair):
    client.patch(f"/peer-pairs/{pair}", json={"status": "approved"})
    statuses = [p["status"] for p in client.get(f"/classrooms/{CLASS}/peer-pairs").json()["pairs"]]
    assert statuses == sorted(statuses, key=["suggested", "approved", "rejected"].index)


def test_status_filter(pair):
    client.patch(f"/peer-pairs/{pair}", json={"status": "approved"})
    approved = client.get(f"/classrooms/{CLASS}/peer-pairs", params={"status": "approved"}).json()["pairs"]
    assert pair in {p["pair_id"] for p in approved}
    assert all(p["status"] == "approved" for p in approved)
    assert client.get(f"/classrooms/{CLASS}/peer-pairs", params={"status": "maybe"}).status_code == 422


def test_unknown_classroom_is_404():
    assert client.get("/classrooms/nope/peer-pairs").status_code == 404


# ---- deciding ---------------------------------------------------------------------------

def test_approve_saves_who_and_when(pair):
    before = client.get(f"/classrooms/{CLASS}/peer-pairs").json()["pending"]
    r = client.patch(f"/peer-pairs/{pair}", json={"status": "approved"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved" and r.json()["status_label"] == "Approved"
    saved = _saved(pair)
    assert saved["status"] == "approved"
    assert saved["decided_by"] == TEST_ADMIN.email and saved["decided_at"] is not None
    assert client.get(f"/classrooms/{CLASS}/peer-pairs").json()["pending"] == before - 1


def test_reject(pair):
    r = client.patch(f"/peer-pairs/{pair}", json={"status": "rejected"})
    assert r.status_code == 200 and _saved(pair)["status"] == "rejected"


def test_a_decision_is_made_once(pair):
    assert client.patch(f"/peer-pairs/{pair}", json={"status": "approved"}).status_code == 200
    r = client.patch(f"/peer-pairs/{pair}", json={"status": "rejected"})
    assert r.status_code == 409 and r.json()["detail"] == "this pair was already approved"
    assert _saved(pair)["status"] == "approved"


@pytest.mark.parametrize("body", [{"status": "suggested"}, {"status": "maybe"}, {}, {"status": "approved", "x": 1}])
def test_only_approved_or_rejected(pair, body):
    r = client.patch(f"/peer-pairs/{pair}", json=body)
    if body == {"status": "approved", "x": 1}:
        assert r.status_code == 200            # extra fields are ignored, the decision still counts
    else:
        assert r.status_code == 422
        assert _saved(pair)["status"] == "suggested"


@pytest.mark.parametrize("pid", [str(uuid.uuid4()), "not-a-uuid"])
def test_unknown_pair_is_404(pid):
    assert client.patch(f"/peer-pairs/{pid}", json={"status": "approved"}).status_code == 404


# ---- with real logins -------------------------------------------------------------------

@pytest.fixture
def tokens():
    """Tokens for the demo teacher (owns the demo classes) and a second teacher who doesn't."""
    settings.jwt_secret = settings.jwt_secret or "test-secret-" + "x" * 40
    tag = uuid.uuid4().hex[:8]
    other = f"test-teacher-{tag}"
    db.execute("INSERT INTO teacher (id, name) VALUES (%s, 'Other Teacher')", (other,))
    out = {}
    for name, teacher in [("demo", "demo-teacher"), ("other", other)]:
        uid = db.execute("""INSERT INTO app_user (email, password_hash, role, teacher_id, display_name)
                            VALUES (%s, 'unused', 'teacher', %s, %s) RETURNING id""",
                         (f"{name}-{tag}@sass.test", teacher, name))[0]["id"]
        out[name] = ({"Authorization": f"Bearer {auth.create_token(uid, 'teacher')}"}, f"{name}-{tag}@sass.test")
    yield out
    db.execute("DELETE FROM app_user WHERE email LIKE %s", (f"%-{tag}@sass.test",))
    db.execute("DELETE FROM teacher WHERE id = %s", (other,))


@pytest.mark.real_auth
def test_no_login_is_401(pair):
    assert client.get(f"/classrooms/{CLASS}/peer-pairs").status_code == 401
    assert client.patch(f"/peer-pairs/{pair}", json={"status": "approved"}).status_code == 401
    assert _saved(pair)["status"] == "suggested"


@pytest.mark.real_auth
def test_another_teacher_cannot_see_or_decide(pair, tokens):
    headers, _ = tokens["other"]
    assert client.get(f"/classrooms/{CLASS}/peer-pairs", headers=headers).status_code == 403
    assert client.patch(f"/peer-pairs/{pair}", json={"status": "approved"}, headers=headers).status_code == 403
    assert _saved(pair)["status"] == "suggested"


@pytest.mark.real_auth
def test_the_class_teacher_decides_and_is_recorded(pair, tokens):
    headers, email = tokens["demo"]
    assert client.get(f"/classrooms/{CLASS}/peer-pairs", headers=headers).status_code == 200
    r = client.patch(f"/peer-pairs/{pair}", json={"status": "rejected"}, headers=headers)
    assert r.status_code == 200 and r.json()["decided_by"] == email
    assert _saved(pair)["decided_by"] == email
