import threading
from datetime import datetime, timedelta, timezone

import pytest
from fakes import essay_row, label_row, write_essays, write_labels
from fastapi.testclient import TestClient
from test_app import AUTH, ESSAY, HOOK, MemoryWriter, event

from agentic.analytics.memory import InMemoryAnalyticsWriter, InMemoryResultsReader
from agentic.analytics.models import VerdictRow
from agentic.config import Settings
from agentic.service.analysis_scheduler import AnalysisScheduler
from agentic.service.app import create_app

T0 = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
CONCEPT = "9-10.W.3.A"
COURSE = "classroom_1.01"


class FakeTimer:
    """Stands in for threading.Timer: fire() is the delay elapsing."""
    made: list = []

    def __init__(self, interval, function, args=()):
        self.interval, self.function, self.args = interval, function, args
        self.cancelled = self.started = False
        self.daemon = False
        FakeTimer.made.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.function(*self.args)


@pytest.fixture(autouse=True)
def _timers():
    FakeTimer.made = []


def live():
    return [t for t in FakeTimer.made if t.started and not t.cancelled]


def vr(student, n, level):
    due = T0 + timedelta(days=7 * n)
    return VerdictRow(
        student_id=student, classroom_id="c1", submission_id=f"{student}-{n}", due_at=due, submitted_at=due,
        submission_type="essay", standard_code=CONCEPT + ".a",
        verdict="growth_area" if level else "meets",
        support_level={1: "approaching", 2: "developing", 3: "beginning"}.get(level), notable_strength=False,
        confidence=0.8, finding_condition=None, overruled=False)


def rows():
    out = [vr("s1", n, 2) for n in range(3)]  # a growth area three times: needs attention
    out += [vr("s2", n, 0) for n in range(3)]
    return out


def inline(fn):
    fn()


def seed(tmp_path):
    write_labels(tmp_path, "1.01", [label_row(student_hash="a" * 64, grade=9)])
    write_essays(tmp_path, "1.01", [essay_row(
        student_hash="a" * 64, paragraphs=[ESSAY, "Second para.", "Third para."], course_id=COURSE,
        assignment_id="asg-classroom_1.01-essay1", submission_id="sub-1")])
    return Settings(fake_llm=True, data_dir=tmp_path, classroom="1.01", agentic_api_token="test-token",
                    webhook_secret="test-secret")


@pytest.fixture
def env(tmp_path):
    s = seed(tmp_path)
    reader = InMemoryResultsReader(rows(), courses={COURSE: "c1"})
    writer = InMemoryAnalyticsWriter()
    app = create_app(s, writer_factory=lambda _s: MemoryWriter(), analytics_reader_factory=lambda _s: reader,
                     analytics_writer_factory=lambda _s: writer, analysis_timer_factory=FakeTimer,
                     analysis_spawn=inline)
    return TestClient(app), writer, app


def post(client, body):
    return client.post("/trend-analyses", json=body, headers=AUTH)


def test_classroom_analysis_is_202_and_run_succeeds(env):
    client, writer, _ = env
    r = post(client, {"scope": "classroom", "course_id": COURSE})
    assert r.status_code == 202 and r.json()["duplicate"] is False and r.json()["ignored"] is False
    run_id = r.json()["run_id"]
    run = client.get(f"/runs/{run_id}", headers=AUTH).json()
    assert run["run_type"] == "trend_analysis" and run["trigger"] == "manual" and run["status"] == "succeeded"
    assert run["started_at"] and run["finished_at"] and run["error"] is None
    assert [str(x.run_id) for x in writer.started] == [run_id]  # one id for the RunStore and the review_run row
    assert writer.started[0].classroom_id == "c1" and writer.started[0].trigger == "manual"
    assert {st.student_id for st in writer.saved[0].statuses} == {"s1", "s2"}


def test_student_analysis(env):
    client, writer, _ = env
    as_of = (T0 + timedelta(days=30)).isoformat()
    r = post(client, {"scope": "student", "course_id": COURSE, "user_id": "s1", "as_of": as_of})
    assert r.status_code == 202
    run = client.get(f"/runs/{r.json()['run_id']}", headers=AUTH).json()
    assert run["status"] == "succeeded"
    started = writer.started[0]
    assert (started.scope, started.student_id, started.as_of) == ("student", "s1", T0 + timedelta(days=30))
    assert {st.student_id for st in writer.saved[0].statuses} == {"s1"}


def test_unknown_course_is_422_and_starts_nothing(env):
    client, writer, app = env
    r = post(client, {"scope": "classroom", "course_id": "nope"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_course"
    assert writer.started == [] and app.state.runs._runs == {}


def test_student_scope_needs_user_id(env):
    client, writer, _ = env
    r = post(client, {"scope": "student", "course_id": COURSE})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"
    assert "body.user_id" in r.json()["error"]["details"]["fields"]
    assert writer.started == []


def test_naive_as_of_and_bad_scope_are_422(env):
    client, _, _ = env
    assert post(client, {"scope": "classroom", "course_id": COURSE,
                         "as_of": "2026-10-01T00:00:00"}).status_code == 422
    assert post(client, {"scope": "galaxy", "course_id": COURSE}).status_code == 422


def test_requires_bearer_token(env):
    client, _, _ = env
    assert client.post("/trend-analyses", json={"scope": "classroom", "course_id": COURSE}).status_code == 401


def test_failed_analysis_reports_the_class_name_only(tmp_path):
    class Boom(InMemoryAnalyticsWriter):
        def save(self, run, *a):
            raise KeyError("Secret essay sentence")

    reader = InMemoryResultsReader(rows(), courses={COURSE: "c1"})
    writer = Boom()
    client = TestClient(create_app(seed(tmp_path), analytics_reader_factory=lambda _s: reader,
                                   analytics_writer_factory=lambda _s: writer, analysis_timer_factory=FakeTimer,
                                   analysis_spawn=inline))
    r = post(client, {"scope": "classroom", "course_id": COURSE})
    run = client.get(f"/runs/{r.json()['run_id']}", headers=AUTH).json()
    assert run["status"] == "failed" and run["error"] == "KeyError" and run["finished_at"]
    assert [e for _, e in writer.failed] == ["KeyError"]


def test_settings_are_snapshotted_at_run_start(env):
    client, writer, _ = env
    post(client, {"scope": "classroom", "course_id": COURSE})
    assert {st.student_id: st.status for st in writer.saved[-1].statuses}["s1"] == "needs_attention"
    for key, value in (("attention_window", 4), ("attention_hits", 4)):  # needs 4 growth areas of the last 4
        assert client.put(f"/settings/{key}", json={"value": value, "changed_by": "t"}, headers=AUTH).status_code == 200
    post(client, {"scope": "classroom", "course_id": COURSE})
    assert {st.student_id: st.status for st in writer.saved[-1].statuses}["s1"] != "needs_attention"


# --- the scheduler ---

def make(run_fn):
    return AnalysisScheduler(run_fn, delay_s=60.0, timer_factory=FakeTimer, spawn=inline)


def test_ten_debounced_calls_give_one_run():
    calls = []
    sched = make(lambda c, t: calls.append((c, t)))
    for _ in range(10):
        sched.debounce("c1")
    assert len(live()) == 1 and live()[0].interval == 60.0 and calls == []
    for t in FakeTimer.made:
        t.fire()  # the cancelled ones do nothing
    assert calls == [("c1", "after_review")]


def test_debounce_is_per_classroom():
    calls = []
    sched = make(lambda c, t: calls.append(c))
    sched.debounce("c1")
    sched.debounce("c2")
    for t in live():
        t.fire()
    assert sorted(calls) == ["c1", "c2"]


def test_request_during_a_run_gives_exactly_one_follow_up():
    calls = []

    def run_fn(c, trigger):
        calls.append((c, trigger))
        if len(calls) == 1:
            for _ in range(5):  # many requests while running
                sched.request("c1", "after_review")
            sched.request("c2", "after_review")  # another classroom runs independently

    sched = make(run_fn)
    sched.request("c1", "manual")
    assert calls == [("c1", "manual"), ("c2", "after_review"), ("c1", "after_review")]


def test_run_that_fails_does_not_wedge_the_classroom():
    calls = []

    def run_fn(c, trigger):
        calls.append(trigger)
        if len(calls) == 1:
            raise RuntimeError("x")

    sched = make(run_fn)
    sched.request("c1", "manual")  # the error is swallowed: a background run has no caller to tell
    sched.request("c1", "after_review")
    assert calls == ["manual", "after_review"]


def test_threads_never_run_one_classroom_together():
    active, peak, calls = [0], [0], []
    gate, started = threading.Event(), threading.Event()
    lock = threading.Lock()

    def run_fn(c, trigger):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            calls.append(trigger)
        started.set()
        gate.wait(5)
        with lock:
            active[0] -= 1

    sched = AnalysisScheduler(run_fn, delay_s=60.0, timer_factory=FakeTimer)  # real threads
    sched.request("c1", "after_review")
    assert started.wait(5)
    sched.request("c1", "after_review")
    sched.request("c1", "after_review")
    gate.set()
    sched.wait_idle(5)
    assert peak[0] == 1 and len(calls) == 2


def test_exclusive_blocks_a_run_of_the_same_classroom():
    order = []
    sched = AnalysisScheduler(lambda c, t: order.append("auto"), delay_s=60.0, timer_factory=FakeTimer)
    released = threading.Event()

    def manual():
        with sched.exclusive("c1"):
            sched.request("c1", "after_review")  # starts, but waits for the manual run to end
            released.wait(0.2)
            order.append("manual")

    manual()
    sched.wait_idle(5)
    assert order == ["manual", "auto"]


# --- automatic runs through the app ---

def test_sweep_ending_triggers_one_analysis(env):
    client, writer, _ = env
    assert client.post("/sweeps", json={"course_id": COURSE}, headers=AUTH).status_code == 202
    assert [x.trigger for x in writer.started] == ["after_review"]
    assert writer.started[0].classroom_id == "c1" and writer.started[0].scope == "classroom"


def test_webhook_review_debounces_an_analysis(env):
    client, writer, _ = env
    assert client.post("/events/submission-graded", json=event(), headers=HOOK).status_code == 202
    assert len(live()) == 1 and writer.started == []
    live()[0].fire()
    assert [x.trigger for x in writer.started] == ["after_review"]


def test_automatic_run_is_visible_in_the_run_store(env):
    client, writer, app = env
    client.post("/sweeps", json={"course_id": COURSE}, headers=AUTH)
    analyses = [r for r in app.state.runs._runs.values() if r.run_type == "trend_analysis"]
    assert len(analyses) == 1 and analyses[0].status == "succeeded" and analyses[0].trigger == "after_review"
    assert str(writer.started[0].run_id) == analyses[0].run_id


def test_app_without_analytics_adapters_schedules_nothing(tmp_path):
    client = TestClient(create_app(seed(tmp_path), writer_factory=lambda _s: MemoryWriter(),
                                   analysis_timer_factory=FakeTimer))
    assert client.post("/events/submission-graded", json=event(), headers=HOOK).status_code == 202
    assert client.post("/sweeps", json={"course_id": COURSE}, headers=AUTH).status_code == 202
    assert FakeTimer.made == []


def test_broken_analytics_path_leaves_a_failed_run_and_the_review_unchanged(tmp_path):
    class Down(InMemoryResultsReader):
        def classroom_for_course(self, lms_course_id):
            raise ConnectionError("password=secret")

    reader = Down(rows())
    app = create_app(seed(tmp_path), writer_factory=lambda _s: MemoryWriter(),
                     analytics_reader_factory=lambda _s: reader, analytics_writer_factory=lambda _s: InMemoryAnalyticsWriter(),
                     analysis_timer_factory=FakeTimer, analysis_spawn=inline)
    client = TestClient(app)
    r = client.post("/events/submission-graded", json=event(), headers=HOOK)
    assert client.get(f"/runs/{r.json()['run_id']}", headers=AUTH).json()["status"] == "succeeded"
    traces = [x for x in app.state.runs._runs.values() if x.run_type == "trend_analysis"]
    assert len(traces) == 1 and traces[0].status == "failed" and traces[0].error == "ConnectionError"
    assert FakeTimer.made == []


def test_course_without_a_classroom_leaves_a_failed_run(tmp_path):
    reader = InMemoryResultsReader(rows(), courses={"other-course": "c1"})  # COURSE maps to nothing
    app = create_app(seed(tmp_path), writer_factory=lambda _s: MemoryWriter(),
                     analytics_reader_factory=lambda _s: reader, analytics_writer_factory=lambda _s: InMemoryAnalyticsWriter(),
                     analysis_timer_factory=FakeTimer, analysis_spawn=inline)
    client = TestClient(app)
    assert client.post("/events/submission-graded", json=event(), headers=HOOK).status_code == 202
    traces = [x for x in app.state.runs._runs.values() if x.run_type == "trend_analysis"]
    assert [(x.status, x.error) for x in traces] == [("failed", "UnknownClassroom")]
