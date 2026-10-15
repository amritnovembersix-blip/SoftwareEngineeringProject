"""Endpoint tests against the real database. Skipped when Postgres isn't reachable
or hasn't been filled with Justin's demo data (seed-demo, load-standards, review --persist)."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

client = TestClient(app)


def _has_demo_data() -> bool:
    try:
        return bool(db.query("SELECT 1 FROM finding LIMIT 1"))
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_demo_data(), reason="database not running or not filled")


def test_classrooms():
    rows = client.get("/classrooms").json()
    assert any(r["id"] == "classroom_1.01" for r in rows)


def test_students_in_classroom():
    rows = client.get("/classrooms/classroom_1.01/students").json()
    assert rows and {"student_id", "review_status", "finding_count"} <= rows[0].keys()


def test_unknown_classroom_is_404():
    assert client.get("/classrooms/nope/students").status_code == 404


def test_findings_for_a_student():
    student = client.get("/classrooms/classroom_1.01/students").json()[0]["student_id"]
    findings = client.get(f"/students/{student}/findings").json()
    assert findings
    f = findings[0]
    assert f["standard_code"] and f["summary"]
    assert f["evidence"] and "paragraph" in f["evidence"][0]


def test_unknown_student_is_404():
    assert client.get("/students/nope/findings").status_code == 404
