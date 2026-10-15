import pytest
from fakes import essay_row, label_row, write_essays, write_labels
from fastapi.testclient import TestClient
from test_app import AUTH, ESSAY, MemoryWriter

from agentic.config import Settings
from agentic.service.app import create_app

STUDENTS = ["a" * 64, "b" * 64, "c" * 64]


def _classroom(data, version, students):
    write_labels(data, version, [label_row(student_hash=h, grade=9) for h in students])
    write_essays(data, version, [essay_row(
        student_hash=h, paragraphs=[ESSAY, "Second para.", "Third para."], course_id=f"classroom_{version}",
        assignment_id=f"asg-classroom_{version}-essay1", submission_id=f"sub-{version}-{h[0]}")
        for h in students])


@pytest.fixture
def env(tmp_path):
    _classroom(tmp_path, "1.01", STUDENTS)
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", webhook_secret="x")
    writer = MemoryWriter()
    return TestClient(create_app(s, writer_factory=lambda _s: writer)), writer, s


def _run(client, body):
    r = client.post("/sweeps", json=body, headers=AUTH)
    assert r.status_code == 202, r.text
    return client.get(f"/runs/{r.json()['run_id']}", headers=AUTH).json()  # background task already ran


def test_sweep_reviews_every_student_in_the_course(env):
    client, writer, _ = env
    run = _run(client, {"course_id": "classroom_1.01"})
    assert run["status"] == "succeeded" and run["trigger"] == "manual"
    assert (run["submission_count"], run["completed_count"], run["skipped_count"]) == (3, 3, 0)
    assert sorted(r.submission_ref.user_id for r, _ in writer.saved) == STUDENTS
    assert {t for _, t in writer.saved} == {"manual"}


def test_second_sweep_skips_already_reviewed(env):
    client, writer, _ = env
    _run(client, {"course_id": "classroom_1.01"})
    run = _run(client, {"course_id": "classroom_1.01"})
    assert (run["completed_count"], run["skipped_count"]) == (0, 3)
    assert {k["reason"] for k in run["skips"]} == {"already_reviewed"}
    assert len(writer.saved) == 3


def test_sweep_without_body_covers_every_classroom(env, tmp_path):
    client, writer, _ = env
    _classroom(tmp_path, "1.02", ["d" * 64])
    r = client.post("/sweeps", headers=AUTH)
    assert r.status_code == 202
    run = client.get(f"/runs/{r.json()['run_id']}", headers=AUTH).json()
    assert run["submission_count"] == 4 and run["completed_count"] == 4
    assert {r.submission_ref.course_id for r, _ in writer.saved} == {"classroom_1.01", "classroom_1.02"}


def test_sweep_one_assignment(env):
    client, _, _ = env
    run = _run(client, {"course_id": "classroom_1.01", "assignment_id": "asg-classroom_1.01-essay1"})
    assert run["completed_count"] == 3
    run = _run(client, {"course_id": "classroom_1.01", "assignment_id": "no-such-assignment"})
    assert run["submission_count"] == 0 and run["status"] == "succeeded"


def test_sweep_unknown_course_is_422(env):
    client, writer, _ = env
    r = client.post("/sweeps", json={"course_id": "classroom_9.99"}, headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_course"
    assert writer.saved == []


def test_assignment_without_course_is_422(env):
    client, _, _ = env
    r = client.post("/sweeps", json={"assignment_id": "x"}, headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"


def test_sweep_needs_service_token(env):
    client, _, _ = env
    assert client.post("/sweeps", json={"course_id": "classroom_1.01"}).status_code == 401


def test_one_failure_does_not_stop_the_sweep_and_stays_content_free(env):
    _, _, s = env
    writer = MemoryWriter()

    class Flaky:
        def __init__(self, real):
            self.real = real

        def run(self, ref):
            if ref.user_id == STUDENTS[1]:
                raise RuntimeError(ESSAY)
            return self.real.run(ref)

    from agentic import wiring

    client = TestClient(create_app(s, writer_factory=lambda _s: writer,
                                   reviewer_factory=lambda st, c: Flaky(wiring.build_course_reviewer(st, c))))
    r = client.post("/sweeps", json={"course_id": "classroom_1.01"}, headers=AUTH)
    run = client.get(f"/runs/{r.json()['run_id']}", headers=AUTH)
    body = run.json()
    assert body["status"] == "failed" and body["completed_count"] == 2
    assert body["error"] == "1 of 3 reviews failed (RuntimeError)"
    assert ESSAY not in run.text and len(writer.saved) == 2


def test_persist_off_reviews_without_writer(env):
    _, _, s = env
    calls = []
    client = TestClient(create_app(s.model_copy(update={"persist_results": False}),
                                   writer_factory=lambda _s: calls.append(1)))
    run = _run(client, {"course_id": "classroom_1.01"})
    assert run["completed_count"] == 3 and calls == []


def test_sweep_while_one_is_running_returns_it(env):
    client, writer, _ = env
    runs = client.app.state.runs
    running, _ = runs.create("manual", 3, ("classroom_1.01", None))
    runs.update(running.run_id, status="running")
    r = client.post("/sweeps", json={"course_id": "classroom_1.01"}, headers=AUTH).json()
    assert r == {"run_id": running.run_id, "duplicate": True, "ignored": False, "reason": None}
    assert writer.saved == []  # nothing new started
    other = client.post("/sweeps", json={"course_id": "classroom_1.01", "assignment_id": "x"}, headers=AUTH).json()
    assert other["duplicate"] is False  # a different scope is a different sweep


def test_finished_sweep_does_not_block_a_new_one(env):
    client, _, _ = env
    first = client.post("/sweeps", json={"course_id": "classroom_1.01"}, headers=AUTH).json()
    second = client.post("/sweeps", json={"course_id": "classroom_1.01"}, headers=AUTH).json()
    assert second["duplicate"] is False and second["run_id"] != first["run_id"]
