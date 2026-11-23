"""What the teacher sees while and after a class review runs: counts and one sentence.

The run dictionaries below have exactly the shape the agentic service returns (service/runs.py):
failures only appear as "N of M reviews failed (ErrorClass)", and a run that hit the AI's daily
limit is marked paused = "daily_quota"."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import agentic_client
from app.agentic_client import AgenticError
from app.main import app
from app.routers.reviews import describe

client = TestClient(app)


def run(status, total=18, completed=0, skipped=0, error=None, paused=None):
    return {"run_id": "r", "status": status, "submission_count": total, "completed_count": completed,
            "skipped_count": skipped, "error": error, "paused": paused}


def test_running():
    d = describe(run("running", completed=5, skipped=2))
    assert (d["done"], d["failed"], d["not_started"], d["quota_reached"]) == (7, 0, 0, False)
    assert d["message"] == "Reviewing… 7 of 18 done."


def test_all_done():
    d = describe(run("succeeded", completed=15, skipped=3))
    assert (d["done"], d["failed"], d["not_started"]) == (18, 0, 0)
    assert d["message"] == "All 18 essays reviewed."


def test_one_failed():
    d = describe(run("failed", completed=12, skipped=5, error="1 of 18 reviews failed (TimeoutError)"))
    assert (d["done"], d["failed"], d["not_started"]) == (17, 1, 0)
    assert d["message"] == "17 of 18 done, 1 failed. Click Review class again to retry; reviewed essays are kept."


def test_daily_quota_ran_out():
    d = describe(run("succeeded", completed=12, paused="daily_quota"))
    assert (d["done"], d["failed"], d["not_started"], d["quota_reached"]) == (12, 0, 6, True)
    assert d["message"] == ("12 of 18 done, 6 waiting because the AI's daily limit was reached. "
                            "Click Review class again tomorrow to finish; reviewed essays are kept.")


def test_quota_and_a_failure_together():
    d = describe(run("failed", completed=10, error="2 of 18 reviews failed (ValueError)", paused="daily_quota"))
    assert (d["done"], d["failed"], d["not_started"]) == (10, 2, 6)
    assert d["message"].startswith("10 of 18 done, 2 failed, 6 waiting because the AI's daily limit was reached.")


def test_whole_run_broke_before_reviewing():
    d = describe(run("failed", completed=0, error="OperationalError"))
    assert (d["done"], d["failed"], d["not_started"]) == (0, 0, 18)
    assert d["message"] == "0 of 18 done, 18 not reviewed. Click Review class again to retry; reviewed essays are kept."


def test_one_essay_wording():
    assert describe(run("succeeded", total=1, completed=1))["message"] == "All 1 essay reviewed."


def test_counts_never_go_negative():
    d = describe(run("failed", total=3, completed=3, error="2 of 3 reviews failed (X)"))
    assert d["not_started"] == 0


def test_no_percentages_and_no_essay_text():
    d = describe(run("failed", completed=12, error="1 of 18 reviews failed (TimeoutError)"))
    assert "%" not in d["message"] and "percent" not in d["message"].lower()


# ---- the endpoint and the agentic client ------------------------------------------------

def test_progress_endpoint_returns_the_message(monkeypatch):
    monkeypatch.setattr(agentic_client, "get_run",
                        lambda rid: run("succeeded", completed=12, paused="daily_quota") | {"run_id": rid})
    body = client.get("/reviews/abc").json()
    assert body["run_id"] == "abc" and body["quota_reached"] is True and body["not_started"] == 6


class _Resp:
    def __init__(self, code):
        self.status_code = code

    def json(self):
        return {}


@pytest.mark.parametrize("code, expected", [(429, 429), (500, 502), (503, 502)])
def test_agentic_busy_or_broken(monkeypatch, code, expected):
    monkeypatch.setattr(httpx, "request", lambda *a, **k: _Resp(code))
    with pytest.raises(AgenticError) as err:
        agentic_client.get_run("x")
    assert err.value.status == expected
    if code == 429:
        assert err.value.message == "the AI service is busy right now; try again in a minute"
