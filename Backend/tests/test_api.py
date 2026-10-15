"""Endpoint tests against the real database. Each test skips when the data it needs isn't there:
the classroom and enrollments come from seed-demo; findings only exist after a review."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

client = TestClient(app)


def _count(sql: str) -> int:
    try:
        return db.query(sql)[0]["n"]
    except Exception:
        return 0


HAS_ENROLLMENT = _count("SELECT count(*) AS n FROM enrollment WHERE classroom_id = 'classroom_1.01'") > 0
HAS_FINDINGS = _count("SELECT count(*) AS n FROM finding") > 0
needs_seed = pytest.mark.skipif(not HAS_ENROLLMENT, reason="classroom_1.01 not seeded with enrollments")
needs_review = pytest.mark.skipif(not HAS_FINDINGS, reason="no reviews saved yet")


@needs_seed
def test_classrooms_count_enrolled_students():
    rows = client.get("/classrooms").json()
    c = next(r for r in rows if r["id"] == "classroom_1.01")
    enrolled = _count("SELECT count(*) AS n FROM enrollment WHERE classroom_id = 'classroom_1.01'")
    assert c["student_count"] == enrolled
    assert 0 <= c["reviewed_count"] <= enrolled


@needs_seed
def test_students_listed_from_enrollment_even_before_review():
    rows = client.get("/classrooms/classroom_1.01/students").json()
    enrolled = _count("SELECT count(*) AS n FROM enrollment WHERE classroom_id = 'classroom_1.01'")
    assert len(rows) == enrolled
    assert {"student_id", "review_status", "finding_count"} <= rows[0].keys()
    for r in rows:
        if r["review_status"] == "not_reviewed":
            assert r["finding_count"] == 0 and r["submission_id"] is None


@needs_seed
def test_unknown_classroom_is_404():
    assert client.get("/classrooms/nope/students").status_code == 404


@needs_review
def test_findings_for_a_reviewed_student():
    students = client.get("/classrooms/classroom_1.01/students").json()
    reviewed = next(s for s in students if s["review_status"] == "succeeded" and s["finding_count"] > 0)
    findings = client.get(f"/students/{reviewed['student_id']}/findings").json()
    assert len(findings) == reviewed["finding_count"]
    f = findings[0]
    assert f["standard_code"] and f["summary"]
    assert f["evidence"] and "paragraph" in f["evidence"][0]


@needs_seed
def test_unknown_student_is_404():
    assert client.get("/students/nope/findings").status_code == 404
