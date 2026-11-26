"""Findings extras: support level and confidence on each finding, a student's strengths, and the
teacher's answer to a finding (POST /findings/{id}/responses).

Expected values come from Justin's tables (finding, verdict, teacher_response). Every answer a
test saves is deleted again afterwards."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.config import settings
from app.main import app
from app.routers.findings import confidence_label

client = TestClient(app)


def _rows(sql, params=()):
    try:
        return db.query(sql, params)
    except db.DatabaseUnavailable:
        return []


STRONG = _rows("""SELECT s.student_id, a.id AS assignment_id FROM verdict v
                  JOIN submission s ON s.id = v.submission_id JOIN assignment a ON a.id = s.assignment_id
                  JOIN classroom c ON c.id = a.classroom_id
                  WHERE v.notable_strength AND c.teacher_id = 'demo-teacher' ORDER BY s.student_id LIMIT 1""")
FINDING = _rows("""SELECT f.id, s.student_id FROM finding f JOIN submission s ON s.id = f.submission_id
                   JOIN assignment a ON a.id = s.assignment_id JOIN classroom c ON c.id = a.classroom_id
                   WHERE c.teacher_id = 'demo-teacher' AND s.review_status = 'succeeded'
                   ORDER BY f.id LIMIT 1""")
needs_data = pytest.mark.skipif(not (STRONG and FINDING), reason="run `python -m agentic_lab fill-demo` first")


def test_confidence_words():
    assert [confidence_label(v) for v in (0.86, 0.8, 0.74, 0.7, 0.62, None)] == \
           ["High", "High", "Medium", "Medium", "Low", None]


# ---- what the findings and strengths say ------------------------------------------------

@needs_data
def test_each_finding_has_support_level_and_confidence_from_the_review():
    sid = FINDING[0]["student_id"]
    expected = {str(r["id"]): r for r in _rows("""
        SELECT f.id, f.support_level, f.confidence FROM finding f JOIN submission s ON s.id = f.submission_id
        WHERE s.student_id = %s AND s.review_status = 'succeeded' AND s.superseded_at IS NULL""", (sid,))}
    body = client.get(f"/students/{sid}/findings").json()
    assert body
    for f in body:
        e = expected[str(f["id"])]
        assert f["support_level"] == e["support_level"]
        assert f["support_level_label"] in {"Approaching", "Developing", "Beginning"}
        assert f["confidence"] == round(e["confidence"], 2)
        assert f["confidence_label"] == confidence_label(e["confidence"])
        assert f["is_draft"] is (f["teacher_response"] is None)   # a draft until the teacher answers
    text = str(body).lower()
    assert "percent" not in text and "%" not in text and "weakness" not in text


@needs_data
def test_strengths_match_the_review_and_filter_by_assignment():
    sid, aid = STRONG[0]["student_id"], STRONG[0]["assignment_id"]
    expected = _rows("""SELECT count(*) AS n FROM verdict v JOIN submission s ON s.id = v.submission_id
                        WHERE s.student_id = %s AND s.review_status = 'succeeded' AND s.superseded_at IS NULL
                          AND v.notable_strength""", (sid,))
    every = client.get(f"/students/{sid}/strengths").json()
    assert len(every) == expected[0]["n"] > 0
    assert all(s["strength_note"] and s["standard_code"] for s in every)
    one = client.get(f"/students/{sid}/strengths", params={"assignment_id": aid}).json()
    assert one and {s["assignment_id"] for s in one} == {aid}
    dates = [s["submitted_at"] for s in every]
    assert dates == sorted(dates, reverse=True)


@needs_data
def test_unknown_student_is_404():
    assert client.get("/students/nope/strengths").status_code == 404


# ---- the teacher's answer (real logins) -------------------------------------------------

@pytest.fixture
def people():
    settings.jwt_secret = settings.jwt_secret or "test-secret-" + "x" * 40
    tag = uuid.uuid4().hex[:8]
    other = f"test-teacher-{tag}"
    db.execute("INSERT INTO teacher (id, name) VALUES (%s, 'Other Teacher')", (other,))
    out = {}
    for name, role, teacher in [("demo", "teacher", "demo-teacher"), ("other", "teacher", other), ("admin", "admin", None)]:
        uid = db.execute("""INSERT INTO app_user (email, password_hash, role, teacher_id, display_name)
                            VALUES (%s, 'unused', %s, %s, %s) RETURNING id""",
                         (f"{name}-{tag}@sass.test", role, teacher, name))[0]["id"]
        out[name] = {"Authorization": f"Bearer {auth.create_token(uid, role)}"}
    yield out
    db.execute("DELETE FROM teacher_response WHERE finding_id = %s", (FINDING[0]["id"],))
    db.execute("DELETE FROM app_user WHERE email LIKE %s", (f"%-{tag}@sass.test",))
    db.execute("DELETE FROM teacher WHERE id = %s", (other,))


def _saved(fid):
    return db.query("SELECT teacher_id, stance, reason FROM teacher_response WHERE finding_id = %s "
                    "ORDER BY responded_at", (fid,))


def _url():
    return f"/findings/{FINDING[0]['id']}/responses"


@needs_data
@pytest.mark.real_auth
def test_teacher_agrees_and_it_is_saved(people):
    r = client.post(_url(), json={"stance": "agree"}, headers=people["demo"])
    assert r.status_code == 201
    assert r.json()["stance"] == "agree" and r.json()["stance_label"] == "Agreed"
    assert [(s["teacher_id"], s["stance"]) for s in _saved(FINDING[0]["id"])] == [("demo-teacher", "agree")]


@needs_data
@pytest.mark.real_auth
def test_the_latest_answer_shows_on_the_finding(people):
    h = people["demo"]
    client.post(_url(), json={"stance": "agree"}, headers=h)
    client.post(_url(), json={"stance": "disagree", "reason": "  The thesis is clear to me.  "}, headers=h)
    saved = _saved(FINDING[0]["id"])
    assert [s["stance"] for s in saved] == ["agree", "disagree"]          # history is kept
    assert saved[-1]["reason"] == "The thesis is clear to me."           # trimmed
    findings = client.get(f"/students/{FINDING[0]['student_id']}/findings", headers=h).json()
    mine = next(f for f in findings if str(f["id"]) == str(FINDING[0]["id"]))
    assert mine["teacher_response"]["stance"] == "disagree"
    assert mine["teacher_response"]["reason"] == "The thesis is clear to me."
    assert mine["is_draft"] is False                                     # answered, so no longer a draft


@needs_data
@pytest.mark.real_auth
@pytest.mark.parametrize("body", [{"stance": "maybe"}, {}, {"stance": "agree", "reason": "x" * 501},
                                  # disagree and dismiss need a reason ("dismiss with a reason")
                                  {"stance": "disagree"}, {"stance": "dismiss"},
                                  {"stance": "dismiss", "reason": "   "}, {"stance": "disagree", "reason": None}])
def test_bad_answers_are_422_and_nothing_is_saved(people, body):
    assert client.post(_url(), json=body, headers=people["demo"]).status_code == 422
    assert _saved(FINDING[0]["id"]) == []


@needs_data
@pytest.mark.real_auth
@pytest.mark.parametrize("who, code", [("other", 403), ("admin", 403), (None, 401)])
def test_only_the_class_teacher_can_answer(people, who, code):
    headers = people[who] if who else {}
    body = {"stance": "dismiss", "reason": "Not what this essay shows."}
    assert client.post(_url(), json=body, headers=headers).status_code == code
    assert _saved(FINDING[0]["id"]) == []


@needs_data
@pytest.mark.real_auth
@pytest.mark.parametrize("fid", [str(uuid.uuid4()), "not-a-uuid"])
def test_unknown_finding_is_404(people, fid):
    assert client.post(f"/findings/{fid}/responses", json={"stance": "agree"}, headers=people["demo"]).status_code == 404


@needs_data
@pytest.mark.real_auth
def test_dismiss_with_a_reason_is_saved(people):
    r = client.post(_url(), json={"stance": "dismiss", "reason": "Quoted dialogue, not a run-on."},
                    headers=people["demo"])
    assert r.status_code == 201
    assert r.json()["reason"] == "Quoted dialogue, not a run-on."
    assert [s["stance"] for s in _saved(FINDING[0]["id"])] == ["dismiss"]
