import pytest
from fakes import essay_row, label_row, write_essays, write_labels
from fastapi.testclient import TestClient
from test_app import AUTH, ESSAY, MemoryWriter, coursedir_setup

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


def _class_of(tmp_path, n):
    students = [ch * 64 for ch in "defghijklmnop"[:n]]  # distinct first letters: distinct submission IDs
    _classroom(tmp_path, "1.01", students)
    return students


def test_concurrent_sweep_reviews_in_parallel_with_one_reviewer_per_thread(tmp_path):
    import threading
    import time as _time

    from agentic import wiring

    students = _class_of(tmp_path, 8)
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", review_concurrency=4)
    lock = threading.Lock()
    inflight, peak, threads_seen, built = [0], [0], set(), []

    class Slow:
        def __init__(self, real):
            self.real = real

        def run(self, ref):
            with lock:
                inflight[0] += 1
                peak[0] = max(peak[0], inflight[0])
                threads_seen.add(threading.get_ident())
            _time.sleep(0.15)
            try:
                return self.real.run(ref)
            finally:
                with lock:
                    inflight[0] -= 1

    def factory(st, course):
        built.append(threading.get_ident())
        return Slow(wiring.build_course_reviewer(st, course))

    writer = MemoryWriter()
    client = TestClient(create_app(s, writer_factory=lambda _s: writer, reviewer_factory=factory))
    run = _run(client, {"course_id": "classroom_1.01"})
    assert run["status"] == "succeeded" and run["completed_count"] == 8
    assert sorted(r.submission_ref.user_id for r, _ in writer.saved) == sorted(students)
    assert 1 < peak[0] <= 4  # really parallel, never above REVIEW_CONCURRENCY
    assert len(built) == len(set(built)) == len(threads_seen)  # one reviewer per worker thread


def test_concurrent_sweep_gives_each_thread_its_own_writer_and_closes_them(tmp_path):
    _class_of(tmp_path, 6)
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", review_concurrency=3)
    writers = []

    def make(_s):
        w = MemoryWriter()
        writers.append(w)
        return w

    client = TestClient(create_app(s, writer_factory=make))
    run = _run(client, {"course_id": "classroom_1.01"})
    assert run["completed_count"] == 6
    assert 1 <= len(writers) <= 3 and sum(len(w.saved) for w in writers) == 6
    assert all(w.closed == 1 for w in writers)


def test_concurrent_sweep_counts_failures_and_skips(tmp_path):
    from agentic import wiring

    students = _class_of(tmp_path, 6)
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", review_concurrency=3)
    writer = MemoryWriter()

    class Flaky:
        def __init__(self, real):
            self.real = real

        def run(self, ref):
            if ref.user_id in students[:2]:
                raise RuntimeError(ESSAY)
            return self.real.run(ref)

    client = TestClient(create_app(s, writer_factory=lambda _s: writer,
                                   reviewer_factory=lambda st, c: Flaky(wiring.build_course_reviewer(st, c))))
    first = _run(client, {"course_id": "classroom_1.01"})
    assert first["status"] == "failed" and first["completed_count"] == 4
    assert first["error"] == "2 of 6 reviews failed (RuntimeError)" and ESSAY not in str(first)
    second = _run(client, {"course_id": "classroom_1.01"})
    assert second["skipped_count"] == 4 and second["completed_count"] == 0  # reviewed ones skipped


@pytest.mark.db
def test_parallel_sweep_with_postgres_gives_each_review_its_own_connection(tmp_path, monkeypatch):
    """Real-model path: a review builds its reviewer (which registers pgvector types inside a transaction)
    on its own connection, so parallel reviews never interleave transactions on one connection
    (OutOfOrderTransactionNesting). The connection is closed when the review ends."""
    from agentic import wiring

    _class_of(tmp_path, 8)
    s = Settings(fake_llm=False, data_dir=tmp_path, agentic_api_token="test-token", review_concurrency=5,
                 database_url_local=Settings.load().database_url_local)
    fake = s.model_copy(update={"fake_llm": True})
    real_build = wiring.build_course_reviewer
    conns = []

    def build(st, course_id, conn=None, options=None):
        assert conn is not None
        conns.append(conn)
        return real_build(fake, course_id, options=options)  # fake models for the review itself

    monkeypatch.setattr(wiring, "build_course_reviewer", build)
    writer = MemoryWriter()
    client = TestClient(create_app(s, writer_factory=lambda _s: writer))
    run = _run(client, {"course_id": "classroom_1.01"})
    assert run["error"] is None and run["completed_count"] == 8
    assert len({id(c) for c in conns}) == 8 and all(c.closed for c in conns)


def test_sweep_skips_multiple_choice_submissions():
    client, writer, _ = coursedir_setup()
    run = _run(client, {"course_id": "classroom_0.90-control"})
    assert run["status"] == "succeeded"
    assert (run["submission_count"], run["completed_count"], run["skipped_count"]) == (6, 4, 2)
    assert {k["reason"] for k in run["skips"]} == {"unsupported_type"}
    assert not any("a03" in r.submission_ref.assignment_id for r, _ in writer.saved)
    assert len(writer.saved) == 4


def test_sweep_of_a_course_without_multiple_choice_has_no_type_skips():
    client, writer, _ = coursedir_setup()
    run = _run(client, {"course_id": "classroom_0.90-intervention"})
    assert (run["completed_count"], run["skipped_count"]) == (1, 0)


def test_coursedir_sweep_of_unknown_course_is_422_without_paths():
    client, writer, data = coursedir_setup()
    r = client.post("/sweeps", json={"course_id": "classroom_9.99"}, headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_course"
    assert data not in r.text and writer.saved == []


def test_coursedir_sweep_of_unknown_assignment_is_4xx_without_paths():
    client, writer, data = coursedir_setup()
    r = client.post("/sweeps", json={"course_id": "classroom_0.90-control",
                                     "assignment_id": "ela9v2-a99@0.90-control"}, headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_course"
    assert data not in r.text and writer.saved == []


def test_v2_coursedir_sweep_reviews_multiple_choice_instead_of_skipping():
    from test_app import COURSEDIR, SECRET_HDR, TOKEN

    s = Settings(fake_llm=True, data_dir=COURSEDIR, lms_source="coursedir", review_pipeline="v2",
                 agentic_api_token=TOKEN, webhook_secret=SECRET_HDR)
    writer = MemoryWriter()
    client = TestClient(create_app(s, writer_factory=lambda _s: writer))
    run = _run(client, {"course_id": "classroom_0.90-control"})
    assert run["status"] == "succeeded", run
    assert (run["submission_count"], run["completed_count"], run["skipped_count"]) == (6, 6, 0)
    mc = [r for r, _ in writer.saved if "a03" in r.submission_ref.assignment_id]
    assert len(mc) == 2 and all(r.pipeline == "v2" and r.submission_type == "multiple_choice" for r in mc)


def test_mvp_coursedir_sweep_still_skips_multiple_choice():
    from test_app import COURSEDIR, SECRET_HDR, TOKEN

    s = Settings(fake_llm=True, data_dir=COURSEDIR, lms_source="coursedir", review_pipeline="mvp",
                 agentic_api_token=TOKEN, webhook_secret=SECRET_HDR)
    writer = MemoryWriter()
    client = TestClient(create_app(s, writer_factory=lambda _s: writer))
    run = _run(client, {"course_id": "classroom_0.90-control"})
    assert (run["submission_count"], run["completed_count"], run["skipped_count"]) == (6, 4, 2)
    assert {k["reason"] for k in run["skips"]} == {"unsupported_type"}


def test_v2_webhook_for_multiple_choice_is_reviewed():
    from test_app import COURSEDIR, HOOK, SECRET_HDR, TOKEN, _coursedir_event

    s = Settings(fake_llm=True, data_dir=COURSEDIR, lms_source="coursedir", review_pipeline="v2",
                 agentic_api_token=TOKEN, webhook_secret=SECRET_HDR)
    writer = MemoryWriter()
    client = TestClient(create_app(s, writer_factory=lambda _s: writer))
    e = _coursedir_event("ela9v2-a03@0.90-control")
    e["body"]["user_id"] = "b2b2b2b2b2b2b2b2"
    r = client.post("/events/submission-graded", json=e, headers=HOOK)
    run = client.get(f"/runs/{r.json()['run_id']}", headers=AUTH).json()
    assert run["status"] == "succeeded" and run["completed_count"] == 1, run
    assert [r.submission_type for r, _ in writer.saved] == ["multiple_choice"]


def _quota_env(tmp_path, monkeypatch):
    """Three students; the fake chat raises DailyQuotaExceeded once `flag` is on, which the writer
    turns on after the first review is saved. Reviews go through the real review_one path."""
    from agentic.llm import fake as fakemod
    from agentic.llm.base import DailyQuotaExceeded

    _classroom(tmp_path, "1.01", STUDENTS)
    flag = {"on": False}
    real = fakemod.FakeChatModel.complete

    def complete(self, *a, **k):
        if flag["on"]:
            raise DailyQuotaExceeded()
        return real(self, *a, **k)

    monkeypatch.setattr(fakemod.FakeChatModel, "complete", complete)

    class W(MemoryWriter):
        def save(self, result, trigger="manual"):
            flag["on"] = flag["on"] or not flag.setdefault("tripped", False)
            flag["tripped"] = True
            return super().save(result, trigger)

    return flag, W()


@pytest.mark.parametrize("pipeline", ["v2", "mvp"])
def test_daily_quota_pauses_the_sweep_and_leaves_the_rest_unclaimed(tmp_path, monkeypatch, pipeline):
    flag, writer = _quota_env(tmp_path, monkeypatch)
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", review_pipeline=pipeline)
    client = TestClient(create_app(s, writer_factory=lambda _s: writer))
    run = _run(client, {"course_id": "classroom_1.01"})
    assert run["status"] == "succeeded" and run["paused"] == "daily_quota" and run["error"] is None
    assert (run["submission_count"], run["completed_count"]) == (3, 1)
    assert len(writer.saved) == 1  # the rest were never saved or claimed
    flag["on"] = False  # the next trigger resumes the unreviewed ones
    again = _run(client, {"course_id": "classroom_1.01"})
    assert again["paused"] is None and again["completed_count"] == 2 and again["skipped_count"] == 1


def test_unpaused_sweep_reports_no_pause(env):
    client, _, _ = env
    assert _run(client, {"course_id": "classroom_1.01"})["paused"] is None


@pytest.mark.parametrize("pipeline", ["v2", "mvp"])
def test_daily_quota_on_a_webhook_run_pauses_it_and_the_next_event_resumes(tmp_path, monkeypatch, pipeline):
    from test_app import HOOK, event

    _quota_env(tmp_path, monkeypatch)
    flag = {"on": True}  # out of quota from the start

    from agentic.llm import fake as fakemod
    from agentic.llm.base import DailyQuotaExceeded

    real = fakemod.FakeChatModel.complete

    def complete(self, *a, **k):
        if flag["on"]:
            raise DailyQuotaExceeded()
        return real(self, *a, **k)

    monkeypatch.setattr(fakemod.FakeChatModel, "complete", complete)
    writer = MemoryWriter()
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", webhook_secret="test-secret",
                 review_pipeline=pipeline)
    client = TestClient(create_app(s, writer_factory=lambda _s: writer))
    ev = event(submission_id="sub-1.01-a")
    first = client.post("/events/submission-graded", json=ev, headers=HOOK).json()
    run = client.get(f"/runs/{first['run_id']}", headers=AUTH).json()
    assert run["status"] == "succeeded" and run["paused"] == "daily_quota"
    assert run["completed_count"] == 0 and writer.saved == []
    flag["on"] = False
    second = client.post("/events/submission-graded", json=ev, headers=HOOK).json()
    assert second["duplicate"] is False and second["run_id"] != first["run_id"]
    run = client.get(f"/runs/{second['run_id']}", headers=AUTH).json()
    assert run["paused"] is None and run["completed_count"] == 1 and len(writer.saved) == 1


def test_daily_quota_during_verify_pauses_the_run(tmp_path, monkeypatch):
    from agentic.decisions.fake import FakeDecisionModel
    from agentic.llm.base import DailyQuotaExceeded

    _classroom(tmp_path, "1.01", STUDENTS)

    def boom(key):
        raise DailyQuotaExceeded()

    monkeypatch.setattr(wiring_module(), "build_decision", lambda st: FakeDecisionModel(boom))
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", verify=True)
    writer = MemoryWriter()
    client = TestClient(create_app(s, writer_factory=lambda _s: writer))
    run = _run(client, {"course_id": "classroom_1.01"})
    assert run["paused"] == "daily_quota" and run["status"] == "succeeded" and run["completed_count"] == 0
    assert writer.saved == []


def wiring_module():
    from agentic import wiring

    return wiring
