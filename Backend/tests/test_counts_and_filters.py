"""Bugs found after the v2 demo data (22 assignments per class) arrived:
the classroom "reviewed" count went over the class size, and a student's findings mixed every
assignment with no way to pick one."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

client = TestClient(app)

V2 = "classroom_2.00-control"


def _has_v2_reviews() -> bool:
    try:
        return bool(db.query("""SELECT 1 FROM submission s JOIN assignment a ON a.id = s.assignment_id
                                WHERE a.classroom_id = %s AND s.review_status = 'succeeded' LIMIT 1""", (V2,)))
    except db.DatabaseUnavailable:
        return False


needs_v2 = pytest.mark.skipif(not _has_v2_reviews(), reason="run `python -m agentic_lab fill-demo` first")


@needs_v2
def test_reviewed_count_is_students_not_submissions():
    for c in client.get("/classrooms").json():
        assert 0 <= c["reviewed_count"] <= c["student_count"], c
    c = next(c for c in client.get("/classrooms").json() if c["id"] == V2)
    reviewed = {s["student_id"] for s in client.get(f"/classrooms/{V2}/students").json()
                if s["review_status"] == "succeeded"}
    assert c["reviewed_count"] == len(reviewed)


@needs_v2
def test_students_row_says_which_assignment():
    row = next(s for s in client.get(f"/classrooms/{V2}/students").json() if s["review_status"] == "succeeded")
    assert row["assignment_id"] and row["assignment_title"]


@needs_v2
def test_findings_filtered_to_the_assignment_on_the_students_page():
    row = next(s for s in client.get(f"/classrooms/{V2}/students").json()
               if s["review_status"] == "succeeded" and s["finding_count"] > 0)
    every = client.get(f"/students/{row['student_id']}/findings").json()
    one = client.get(f"/students/{row['student_id']}/findings",
                     params={"assignment_id": row["assignment_id"]}).json()
    assert len(every) > len(one) > 0                      # the filter really narrows it down
    assert {f["assignment_id"] for f in one} == {row["assignment_id"]}
    assert len(one) == row["finding_count"]               # matches the number on the students page


@needs_v2
def test_findings_come_newest_assignment_first():
    row = next(s for s in client.get(f"/classrooms/{V2}/students").json() if s["finding_count"] > 0)
    dates = [f["submitted_at"] for f in client.get(f"/students/{row['student_id']}/findings").json()]
    assert dates == sorted(dates, reverse=True)


@needs_v2
def test_unknown_assignment_gives_an_empty_list_not_an_error():
    row = next(s for s in client.get(f"/classrooms/{V2}/students").json() if s["finding_count"] > 0)
    r = client.get(f"/students/{row['student_id']}/findings", params={"assignment_id": "nope"})
    assert r.status_code == 200 and r.json() == []
