"""Review-class endpoints. The agentic service is faked, so these run without it.
They need the database for the classroom lookup and skip when it isn't filled."""
import pytest
from fastapi.testclient import TestClient

from app import agentic_client, db
from app.agentic_client import AgenticError
from app.main import app

client = TestClient(app)


def _has_classroom() -> bool:
    try:
        return bool(db.query("SELECT 1 FROM classroom WHERE id = 'classroom_1.01'"))
    except Exception:
        return False


@pytest.fixture
def fake_agentic(monkeypatch):
    calls = []

    def start_sweep(course_id):
        calls.append(course_id)
        return {"run_id": "run-1", "duplicate": False}

    def get_run(run_id):
        if run_id != "run-1":
            raise AgenticError(404, "no such review run")
        return {"run_id": "run-1", "status": "running", "submission_count": 20,
                "completed_count": 9, "skipped_count": 3, "error": None}

    monkeypatch.setattr(agentic_client, "start_sweep", start_sweep)
    monkeypatch.setattr(agentic_client, "get_run", get_run)
    return calls


@pytest.mark.skipif(not _has_classroom(), reason="database not running or not filled")
def test_start_review_sends_the_lms_course_id(fake_agentic):
    r = client.post("/classrooms/classroom_1.01/reviews")
    assert r.status_code == 202
    assert r.json() == {"run_id": "run-1", "already_running": False}
    assert fake_agentic == ["classroom_1.01"]


@pytest.mark.skipif(not _has_classroom(), reason="database not running or not filled")
def test_start_review_unknown_classroom_is_404(fake_agentic):
    assert client.post("/classrooms/nope/reviews").status_code == 404
    assert fake_agentic == []


def test_progress_adds_completed_and_skipped(fake_agentic):
    body = client.get("/reviews/run-1").json()
    assert body == {"run_id": "run-1", "status": "running", "done": 12, "total": 20, "error": None}


def test_progress_unknown_run_is_404(fake_agentic):
    assert client.get("/reviews/other").status_code == 404
