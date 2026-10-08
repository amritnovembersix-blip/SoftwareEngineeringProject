import pytest
from fakes import essay_row, label_row, write_essays, write_labels
from fastapi.testclient import TestClient

from agentic import wiring
from agentic.config import Settings
from agentic.results.base import SaveOutcome
from agentic.service.app import create_app

TOKEN, SECRET_HDR = "test-token", "test-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HOOK = {"X-SASS-Webhook-Secret": SECRET_HDR}
ESSAY = "Secret essay sentence alpha."


def event(submission_id="sub-1", attempt=1):
    return {"metadata": {"event_name": "submission_graded", "event_time": "2026-10-08T12:00:00Z"},
            "body": {"submission_id": submission_id, "assignment_id": "asg-classroom_1.01-essay1",
                     "course_id": "classroom_1.01", "user_id": "a" * 64, "attempt": attempt}}


class MemoryWriter:
    def __init__(self, duplicate=False):
        self.saved, self.duplicate, self.closed = [], duplicate, 0

    def save(self, result, trigger="manual"):
        self.saved.append((result, trigger))
        return SaveOutcome(status="duplicate" if self.duplicate else "written", review_run_id="r-1")

    def close(self):
        self.closed += 1


@pytest.fixture
def setup(tmp_path):
    write_labels(tmp_path, "1.01", [label_row(student_hash="a" * 64, grade=9)])
    write_essays(tmp_path, "1.01", [essay_row(
        student_hash="a" * 64, paragraphs=[ESSAY, "Second para.", "Third para."],
        course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1", submission_id="sub-1")])
    s = Settings(fake_llm=True, data_dir=tmp_path, classroom="1.01",
                 agentic_api_token=TOKEN, webhook_secret=SECRET_HDR)
    writer = MemoryWriter()
    client = TestClient(create_app(s, writer_factory=lambda _s: writer))
    return client, writer, s


def test_health_needs_no_auth(setup):
    client, _, _ = setup
    assert client.get("/health").json() == {"status": "ok"}


def test_webhook_reviews_and_run_succeeds(setup):
    client, writer, _ = setup
    r = client.post("/events/submission-graded", json=event(), headers=HOOK)
    assert r.status_code == 202
    body = r.json()
    assert body["duplicate"] is False and body["ignored"] is False and body["run_id"]
    run = client.get(f"/runs/{body['run_id']}", headers=AUTH).json()  # background task already ran
    assert run["status"] == "succeeded" and run["trigger"] == "on_grade"
    assert (run["submission_count"], run["completed_count"], run["skipped_count"]) == (1, 1, 0)
    assert run["started_at"] and run["finished_at"]
    result, trigger = writer.saved[0]
    assert trigger == "on_grade" and len(result.findings) == 2
    assert writer.closed == 1


def test_duplicate_event_returns_existing_run(setup):
    client, writer, _ = setup
    first = client.post("/events/submission-graded", json=event(), headers=HOOK).json()
    second = client.post("/events/submission-graded", json=event(), headers=HOOK).json()
    assert second == {**first, "duplicate": True}
    assert len(writer.saved) == 1


def test_new_attempt_is_a_new_run(setup):
    client, _, _ = setup
    a = client.post("/events/submission-graded", json=event(attempt=1), headers=HOOK).json()
    b = client.post("/events/submission-graded", json=event(attempt=2), headers=HOOK).json()
    assert a["run_id"] != b["run_id"] and b["duplicate"] is False


def test_already_persisted_attempt_is_skipped(tmp_path, setup):
    _, _, s = setup
    client = TestClient(create_app(s, writer_factory=lambda _s: MemoryWriter(duplicate=True)))
    run_id = client.post("/events/submission-graded", json=event(), headers=HOOK).json()["run_id"]
    run = client.get(f"/runs/{run_id}", headers=AUTH).json()
    assert run["status"] == "succeeded" and run["skipped_count"] == 1 and run["completed_count"] == 0
    assert run["skips"] == [{"submission_id": "sub-1", "attempt": 1, "reason": "already_reviewed"}]


def test_failed_review_is_content_free_and_retryable(setup):
    client, _, s = setup

    class Boom:
        def run(self, ref):
            raise RuntimeError(ESSAY)

    calls = []

    def factory(_s, _course):
        calls.append(1)
        return Boom()

    client = TestClient(create_app(s, reviewer_factory=factory, writer_factory=lambda _s: MemoryWriter()))
    first = client.post("/events/submission-graded", json=event(), headers=HOOK).json()
    run = client.get(f"/runs/{first['run_id']}", headers=AUTH)
    assert run.json()["status"] == "failed" and run.json()["error"] == "RuntimeError"
    assert ESSAY not in run.text
    retry = client.post("/events/submission-graded", json=event(), headers=HOOK).json()
    assert retry["duplicate"] is False and retry["run_id"] != first["run_id"] and len(calls) == 2


def test_unknown_course_fails_run(setup):
    client, _, _ = setup
    e = event()
    e["body"]["course_id"] = "classroom_9.99"
    run_id = client.post("/events/submission-graded", json=e, headers=HOOK).json()["run_id"]
    assert client.get(f"/runs/{run_id}", headers=AUTH).json()["status"] == "failed"


def test_persist_off_skips_writer(setup):
    _, _, s = setup
    writer = MemoryWriter()
    client = TestClient(create_app(s.model_copy(update={"persist_results": False}),
                                   writer_factory=lambda _s: writer))
    run_id = client.post("/events/submission-graded", json=event(), headers=HOOK).json()["run_id"]
    assert client.get(f"/runs/{run_id}", headers=AUTH).json()["completed_count"] == 1
    assert writer.saved == []


@pytest.mark.parametrize("headers", [{}, {"X-SASS-Webhook-Secret": "wrong"}, AUTH])
def test_webhook_rejects_bad_secret(setup, headers):
    client, writer, _ = setup
    r = client.post("/events/submission-graded", json=event(), headers=headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert writer.saved == []


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, HOOK])
def test_runs_need_service_token(setup, headers):
    client, _, _ = setup
    assert client.get("/runs/x", headers=headers).status_code == 401


def test_blank_secrets_refuse_everything(tmp_path):
    client = TestClient(create_app(Settings(fake_llm=True, data_dir=tmp_path)))
    assert client.post("/events/submission-graded", json=event(),
                       headers={"X-SASS-Webhook-Secret": ""}).status_code == 401
    assert client.get("/review-policy", headers={"Authorization": "Bearer "}).status_code == 401


def test_unknown_run_is_404(setup):
    client, _, _ = setup
    r = client.get("/runs/nope", headers=AUTH)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_invalid_event_is_422_with_field_names_only(setup):
    client, _, _ = setup
    e = event(attempt=0)
    e["body"]["user_id"] = None
    e["metadata"]["event_name"] = ESSAY
    r = client.post("/events/submission-graded", json=e, headers=HOOK)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"
    assert "body.body.attempt" in r.json()["error"]["details"]["fields"]
    assert ESSAY not in r.text


def test_policy_round_trip_and_on_grade_off_ignores_events(setup):
    client, writer, _ = setup
    policy = client.get("/review-policy", headers=AUTH).json()
    assert policy["submission_review"]["on_grade"] is True
    policy["submission_review"]["on_grade"] = False
    policy["submission_review"]["schedules"] = ["0 2 * * *"]
    assert client.put("/review-policy", json=policy, headers=AUTH).json() == policy
    r = client.post("/events/submission-graded", json=event(), headers=HOOK).json()
    assert r == {"run_id": None, "duplicate": False, "ignored": True, "reason": "mode_disabled"}
    assert writer.saved == []


@pytest.mark.parametrize("field,value,code", [
    ("schedules", ["every night"], "invalid_cron"),
    ("schedules", ["0 2 * *"], "invalid_cron"),
    ("timezone", "Mars/Olympus", "invalid_timezone"),
])
def test_invalid_policy_is_422_and_unchanged(setup, field, value, code):
    client, _, _ = setup
    before = client.get("/review-policy", headers=AUTH).json()
    bad = {**before, "submission_review": dict(before["submission_review"])}
    if field == "timezone":
        bad["timezone"] = value
    else:
        bad["submission_review"]["schedules"] = value
    r = client.put("/review-policy", json=bad, headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["code"] == code
    assert client.get("/review-policy", headers=AUTH).json() == before


@pytest.mark.parametrize("path", ["/sweeps", "/trend-analyses"])
def test_unbuilt_triggers_are_501(setup, path):
    client, _, _ = setup
    r = client.post(path, json={}, headers=AUTH)
    assert r.status_code == 501 and r.json()["error"]["code"] == "not_implemented"


def test_course_reviewer_maps_classroom_course_to_version(setup):
    _, _, s = setup
    assert wiring.build_course_reviewer(s, "classroom_1.01").strands == ["W"]
