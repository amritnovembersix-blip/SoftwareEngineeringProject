"""FR6: GET /students/{id}/concepts and /students/{id}/concept-history.

Expected values come from Justin's analysis tables (concept_status_latest), not from the
endpoint's own SQL, so a wrong query can't agree with itself."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.routers.concepts import _skill_code

client = TestClient(app)

STATUSES = ["needs_attention", "emerging", "too_little_evidence", "secure"]
LEVEL_WORDS = {"Meets", "Approaching", "Developing", "Beginning"}


def _rows(sql, params=()):
    try:
        return db.query(sql, params)
    except db.DatabaseUnavailable:
        return []


# A student with at least one "needs attention" concept, and a few others to cross-check.
ATTENTION = _rows("SELECT student_id FROM concept_status_latest WHERE status = 'needs_attention' "
                  "ORDER BY student_id LIMIT 1")
SAMPLE = _rows("SELECT DISTINCT student_id FROM concept_status_latest ORDER BY student_id LIMIT 5")
needs_analysis = pytest.mark.skipif(not ATTENTION, reason="run `python -m agentic_lab fill-demo` first")


def test_skill_code_drops_the_grade():
    assert _skill_code("9-10.W.3.A") == "W.3.A"
    assert _skill_code("3.L.1.A") == "L.1.A"
    assert _skill_code("W.3.A") == "W.3.A"


@needs_analysis
def test_unknown_student_is_404():
    assert client.get("/students/nope/concepts").status_code == 404
    assert client.get("/students/nope/concept-history").status_code == 404


@needs_analysis
def test_every_concept_is_listed_with_labels_and_no_scores():
    sid = ATTENTION[0]["student_id"]
    body = client.get(f"/students/{sid}/concepts").json()
    expected = _rows("SELECT concept_code, status, current_level, basis, trend "
                     "FROM concept_status_latest WHERE student_id = %s", (sid,))
    got = {c["concept_code"]: c for c in body["concepts"]}
    assert set(got) == {e["concept_code"] for e in expected}
    for e in expected:
        c = got[e["concept_code"]]
        assert (c["status"], c["current_level"], c["basis"], c["trend"]) == \
               (e["status"], e["current_level"], e["basis"], e["trend"])
        assert c["level_label"] in LEVEL_WORDS
        assert c["status_label"] and c["strand"]
    # SASS doesn't grade: no percentages or scores anywhere in the response.
    text = str(body).lower()
    assert "percent" not in text and "score" not in text and "%" not in text


@needs_analysis
def test_most_urgent_first_and_summary_adds_up():
    sid = ATTENTION[0]["student_id"]
    body = client.get(f"/students/{sid}/concepts").json()
    order = [STATUSES.index(c["status"]) for c in body["concepts"]]
    assert order == sorted(order)
    assert body["concepts"][0]["status"] == "needs_attention"
    assert sum(body["summary"].values()) == len(body["concepts"])
    assert body["summary"]["needs_attention"] >= 1


@needs_analysis
def test_classroom_filter():
    sid = ATTENTION[0]["student_id"]
    assert client.get(f"/students/{sid}/concepts", params={"classroom_id": "nope"}).json()["concepts"] == []


@needs_analysis
@pytest.mark.parametrize("i", range(5))
def test_history_matches_the_analysis_points(i):
    # The chart must tell the same story as the status: same levels, same order, for every concept.
    sid = SAMPLE[i]["student_id"]
    expected = {(r["classroom_id"], r["concept_code"]): list(r["points"]) for r in _rows(
        "SELECT classroom_id, concept_code, points FROM concept_status_latest WHERE student_id = %s", (sid,))}
    history = client.get(f"/students/{sid}/concept-history").json()
    got = {(h["classroom_id"], h["concept_code"]): [p["level"] for p in h["points"]] for h in history}
    assert got == expected


@needs_analysis
def test_history_points_are_dated_oldest_first_with_monday_weeks():
    sid = ATTENTION[0]["student_id"]
    for h in client.get(f"/students/{sid}/concept-history").json():
        times = [p["submitted_at"] for p in h["points"]]
        assert times == sorted(times)
        for p in h["points"]:
            week = date.fromisoformat(p["week_start"])
            assert week.isoweekday() == 1
            assert week <= datetime.fromisoformat(p["submitted_at"]).date()
            assert p["level_label"] in LEVEL_WORDS and p["assignment_title"]


@needs_analysis
def test_history_for_one_concept():
    sid = ATTENTION[0]["student_id"]
    code = _rows("SELECT concept_code FROM concept_status_latest WHERE student_id = %s "
                 "AND status = 'needs_attention' LIMIT 1", (sid,))[0]["concept_code"]
    history = client.get(f"/students/{sid}/concept-history", params={"concept_code": code}).json()
    assert [h["concept_code"] for h in history] == [code]
    assert history[0]["skill_code"] == _skill_code(code)
