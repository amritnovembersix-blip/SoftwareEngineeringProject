import json
import threading
from pathlib import Path

import pytest

from agentic.coursedata import CourseDir

from agentic_lab.config import LAB_ROOT
from agentic_lab.sim.release import FolderReleaser
from agentic_lab.sim.schedule import due_in_week, schedule
from agentic_lab.sim.settings import load_sim_settings
from agentic_lab.sim.simulator import Simulator
from agentic_lab.sim.state import SentEvent, SimState, load_state, save_state
from agentic_lab.sim.trigger import TriggerError

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic"
CONTROL, INTERVENTION = "2.00-control", "2.00-intervention"


class FakeTrigger:
    """Records each event; with fail_on=n, the n-th call (1-based) raises TriggerError."""

    def __init__(self, fail_on: int | None = None):
        self.events = []
        self.calls = 0
        self.fail_on = fail_on
        self._lock = threading.Lock()

    def send(self, event):
        with self._lock:
            self.calls += 1
            if self.calls == self.fail_on:
                raise TriggerError(f"event rejected for assignment {event.assignment_id}",
                                   assignment_id=event.assignment_id, student_hash=event.user_id, status=500)
            self.events.append(event)
            return f"run-{event.course_id}-{event.submission_id}-{event.attempt}"


class FakeRuns:
    def __init__(self, status="succeeded", unknown=False):
        self.status, self.unknown, self.calls = status, unknown, 0

    def get(self, run_id):
        self.calls += 1
        return None if self.unknown else {"id": run_id, "status": self.status}


def _sim(tmp_path, trigger, versions=(CONTROL,), runs=None, concurrency=1, **kw):
    sim_dir = tmp_path / "sim"
    return Simulator(DATA, list(versions), FolderReleaser(DATA, sim_dir), trigger, runs, sim_dir,
                     concurrency=concurrency, **kw)


def _rows(version, file_id):
    return CourseDir(DATA, version).submissions(file_id)


def test_week_1_sends_one_event_per_row_in_due_order(tmp_path):
    t = FakeTrigger()
    sim = _sim(tmp_path, t)
    sim.reset()
    report = sim.advance()
    a01, a02 = _rows(CONTROL, "ela9v2-a01"), _rows(CONTROL, "ela9v2-a02")
    assert report.week == 1 and report.sent == len(a01) + len(a02) == report.released
    assert report.already_sent == 0
    assert [e.assignment_id for e in t.events] == (
        ["ela9v2-a01@2.00-control"] * len(a01) + ["ela9v2-a02@2.00-control"] * len(a02))
    items = {i.lms_id: i for i in schedule(DATA, CONTROL)}
    for e in t.events:
        assert e.event_time == items[e.assignment_id].event_time
        assert e.course_id == "classroom_2.00-control"
    assert {(e.submission_id, e.user_id, e.attempt) for e in t.events} == {
        (r.submission_id, r.student_hash, r.attempt) for r in a01 + a02}
    state = load_state(tmp_path / "sim")
    assert state.week == 1 and len(state.sent) == len(t.events)


def test_trigger_failure_keeps_week_and_rerun_has_no_duplicates(tmp_path):
    failing = FakeTrigger(fail_on=3)
    sim = _sim(tmp_path, failing)
    sim.reset()
    with pytest.raises(TriggerError):
        sim.advance()
    state = load_state(tmp_path / "sim")
    assert state.week == 0 and len(state.sent) == 2
    ok = FakeTrigger()
    sim2 = _sim(tmp_path, ok)
    report = sim2.advance()
    total = len(_rows(CONTROL, "ela9v2-a01")) + len(_rows(CONTROL, "ela9v2-a02"))
    assert report.week == 1 and report.already_sent == 2 and report.sent == total - 2
    keys = [(e.submission_id, e.attempt) for e in failing.events + ok.events]
    assert len(keys) == len(set(keys)) == total
    assert load_state(tmp_path / "sim").week == 1


def test_failure_with_concurrency_records_in_flight_successes(tmp_path):
    failing = FakeTrigger(fail_on=5)
    sim = _sim(tmp_path, failing, concurrency=4)
    sim.reset()
    with pytest.raises(TriggerError):
        sim.advance()
    state = load_state(tmp_path / "sim")
    assert state.week == 0 and len(state.sent) == len(failing.events)


def test_advance_past_the_last_week(tmp_path):
    t = FakeTrigger()
    sim = _sim(tmp_path, t, concurrency=4)
    sim.reset()
    report = sim.advance(weeks=12)
    total = sum(len(_rows(CONTROL, i.file_id)) for i in schedule(DATA, CONTROL))
    assert report.week == 12 and report.sent == total == report.released
    later = sim.advance(weeks=2)
    assert later.week == 14 and later.released == 0 and later.sent == 0
    assert len(t.events) == total


def test_both_courses_keep_twins_apart_and_i01_goes_to_its_students(tmp_path):
    t = FakeTrigger()
    sim = _sim(tmp_path, t, versions=(CONTROL, INTERVENTION))
    seen = []
    sim.run_through(4)
    t.events.clear()
    sim.advance(progress=lambda week, item, released, sent: seen.append((week, item.lms_id, released, sent)))
    courses = [e.course_id for e in t.events]
    first_intervention = courses.index("classroom_2.00-intervention")
    assert set(courses[:first_intervention]) == {"classroom_2.00-control"}
    assert set(courses[first_intervention:]) == {"classroom_2.00-intervention"}
    assert [s[1] for s in seen] == ["ela9v2-a09@2.00-control", "ela9v2-a10@2.00-control",
                                    "ela9v2-a09@2.00-intervention", "ela9v2-a10@2.00-intervention",
                                    "ela9v2-i01@2.00-intervention"]
    assert all(s[0] == 5 and s[2] == s[3] for s in seen)
    i01 = [e for e in t.events if e.assignment_id == "ela9v2-i01@2.00-intervention"]
    assigned = {r.student_hash for r in _rows(INTERVENTION, "ela9v2-i01")}
    assert i01 and {e.user_id for e in i01} == assigned
    assert len(assigned) < len(CourseDir(DATA, INTERVENTION).roster())
    state = load_state(tmp_path / "sim")
    keys = {(s.course_id, s.submission_id, s.attempt) for s in state.sent}
    assert len(keys) == len(state.sent)


def test_deleted_release_is_rereleased_without_resending(tmp_path):
    t = FakeTrigger()
    sim = _sim(tmp_path, t)
    sim.reset()
    sim.advance(weeks=2)
    sent_before = len(t.events)
    import shutil
    shutil.rmtree(tmp_path / "sim" / "courses")
    seen = []
    report = sim.advance(progress=lambda week, item, released, sent: seen.append((week, item.file_id, sent)))
    past = [i for i in schedule(DATA, CONTROL) if i.week <= 2]
    week3 = due_in_week(schedule(DATA, CONTROL), 3)
    for i in past + week3:
        assert (tmp_path / "sim" / "courses" / CONTROL / "submissions" / f"{i.file_id}.jsonl").is_file()
    past_rows = sum(len(_rows(CONTROL, i.file_id)) for i in past)
    week3_rows = sum(len(_rows(CONTROL, i.file_id)) for i in week3)
    assert report.week == 3 and report.released == past_rows + week3_rows
    assert report.sent == week3_rows and report.already_sent == 0
    assert all(w == 3 for w, _, _ in seen)  # past weeks send nothing
    keys = [(e.submission_id, e.attempt) for e in t.events]
    assert len(keys) == len(set(keys)) == sent_before + week3_rows
    assert {e.assignment_id for e in t.events[sent_before:]} == {i.lms_id for i in week3}


def test_deleted_state_means_week_0(tmp_path):
    sim = _sim(tmp_path, FakeTrigger())
    sim.reset()
    sim.advance()
    (tmp_path / "sim" / "state.json").unlink()
    assert load_state(tmp_path / "sim") == SimState()


def test_reset_empties_sim_dir(tmp_path):
    sim = _sim(tmp_path, FakeTrigger())
    sim.reset()
    sim.advance()
    sim.reset()
    assert sorted(p.name for p in (tmp_path / "sim").iterdir()) == ["state.json"]
    assert load_state(tmp_path / "sim") == SimState(week=0, sent=[])


def test_status_counts_unknown_when_runs_return_none(tmp_path):
    sim = _sim(tmp_path, FakeTrigger(), runs=FakeRuns(unknown=True))
    sim.reset()
    sim.advance()
    rows = sim.status()
    assert [r.file_id for r in rows] == [i.file_id for i in schedule(DATA, CONTROL)]
    a01 = rows[0]
    n = len(_rows(CONTROL, "ela9v2-a01"))
    assert a01.released and a01.sent == n and a01.unknown == n and a01.succeeded == 0
    assert not rows[2].released and rows[2].sent == 0


def test_status_counts_run_states(tmp_path):
    sim = _sim(tmp_path, FakeTrigger(), runs=FakeRuns(status="running"))
    sim.reset()
    sim.advance()
    a01 = sim.status()[0]
    assert a01.running == a01.sent and a01.queued == a01.failed == a01.unknown == 0


def test_status_without_runs_client_is_unknown(tmp_path):
    sim = _sim(tmp_path, FakeTrigger())
    sim.reset()
    sim.advance()
    a01 = sim.status()[0]
    assert a01.unknown == a01.sent > 0


def test_wait_polls_until_done(tmp_path):
    class Flipping(FakeRuns):
        def get(self, run_id):
            self.calls += 1
            return {"status": "running" if self.calls <= 3 else "succeeded"}

    slept = []
    runs = Flipping()
    sim = _sim(tmp_path, FakeTrigger(), runs=runs, sleep=slept.append, clock=lambda: 0.0)
    sim.reset()
    assert sim.advance(wait=True).week == 1
    assert slept and all(s == 2 for s in slept)


def test_wait_unknown_runs_count_as_done(tmp_path):
    sim = _sim(tmp_path, FakeTrigger(), runs=FakeRuns(unknown=True), sleep=lambda s: None, clock=lambda: 0.0)
    sim.reset()
    assert sim.advance(wait=True).week == 1


def test_wait_times_out_naming_the_week(tmp_path):
    now = [0.0]

    def sleep(s):
        now[0] += s

    sim = _sim(tmp_path, FakeTrigger(), runs=FakeRuns(status="queued"), sleep=sleep, clock=lambda: now[0])
    sim.reset()
    with pytest.raises(TimeoutError, match="week 1"):
        sim.advance(wait=True)
    assert load_state(tmp_path / "sim").week == 0


def test_wait_without_runs_client_is_an_error(tmp_path):
    sim = _sim(tmp_path, FakeTrigger())
    sim.reset()
    with pytest.raises(ValueError):
        sim.advance(wait=True)


def test_state_round_trip_and_no_text(tmp_path):
    state = SimState(week=3, sent=[SentEvent(course_id="c", assignment_id="a", submission_id="s", attempt=1,
                                             run_id="r", sent_at="2027-01-18T14:00:00Z")])
    save_state(tmp_path, state)
    assert load_state(tmp_path) == state
    assert set(json.loads((tmp_path / "state.json").read_text())) == {"week", "sent"}
    assert not list(tmp_path.glob("*.tmp"))


def test_state_has_no_student_text(tmp_path):
    sim = _sim(tmp_path, FakeTrigger())
    sim.reset()
    sim.advance()
    raw = (tmp_path / "sim" / "state.json").read_text()
    for row in _rows(CONTROL, "ela9v2-a01"):
        for p in row.paragraphs or []:
            assert p[:40] not in raw


def test_sim_settings_defaults_and_env(tmp_path, monkeypatch):
    for name in ("AGENTIC_URL", "WEBHOOK_SECRET", "AGENTIC_API_TOKEN", "SIM_DIR", "SIM_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)
    s = load_sim_settings(env_file=None)
    assert s.agentic_url == "http://localhost:8000" and s.webhook_secret == "" and s.agentic_api_token == ""
    assert s.sim_dir == LAB_ROOT / "local" / "sim" and s.concurrency == 4
    env = tmp_path / ".env"
    env.write_text("AGENTIC_URL=http://x:9\nSIM_DIR=foo/sim\nSIM_CONCURRENCY=2\nWEBHOOK_SECRET=\n")
    monkeypatch.setenv("SIM_CONCURRENCY", "7")
    s = load_sim_settings(env_file=env)
    assert s.agentic_url == "http://x:9" and s.concurrency == 7 and s.webhook_secret == ""
    assert s.sim_dir.is_absolute() and s.sim_dir.parts[-2:] == ("foo", "sim")
    assert s.sim_dir.parent.parent.name != "agentic_lab"  # relative to the repo root


@pytest.mark.parametrize("bad", ["0", "four"])
def test_sim_settings_bad_concurrency(monkeypatch, bad):
    monkeypatch.setenv("SIM_CONCURRENCY", bad)
    with pytest.raises(ValueError, match="SIM_CONCURRENCY") as e:
        load_sim_settings(env_file=None)
    assert bad not in str(e.value)


def test_run_through_passes_progress_to_advance(tmp_path):
    seen = []
    _sim(tmp_path, FakeTrigger()).run_through(
        4, progress=lambda week, item, released, sent: seen.append(week))
    assert seen and max(seen) == 4


def _data_copy(tmp_path):
    """A throwaway copy of the control course and its curriculum (no answer key), safe to point a reset at."""
    import shutil
    data = tmp_path / "copy" / "data"
    shutil.copytree(DATA / "curricula" / "ela9-v2", data / "curricula" / "ela9-v2")
    shutil.copytree(DATA / "courses" / CONTROL, data / "courses" / CONTROL,
                    ignore=shutil.ignore_patterns("answer_key"))
    return data


@pytest.mark.parametrize("where", ["data", "parent"])
def test_reset_refuses_a_sim_dir_holding_the_data(tmp_path, where):
    data = _data_copy(tmp_path)
    sim_dir = data if where == "data" else data.parent
    sim = Simulator(data, [CONTROL], FolderReleaser(data, sim_dir), FakeTrigger(), None, sim_dir)
    with pytest.raises(ValueError, match="SIM_DIR") as e:
        sim.reset()
    assert str(tmp_path) not in str(e.value)
    assert (data / "courses" / CONTROL / "course.json").is_file()


def test_reset_refuses_a_sim_dir_holding_the_repo_root(tmp_path, monkeypatch):
    from agentic_lab.sim import simulator
    monkeypatch.setattr(simulator, "REPO_ROOT", tmp_path / "outer" / "repo")
    (tmp_path / "outer" / "repo").mkdir(parents=True)
    sim_dir = tmp_path / "outer"
    sim = Simulator(DATA, [CONTROL], FolderReleaser(DATA, sim_dir), FakeTrigger(), None, sim_dir)
    with pytest.raises(ValueError, match="SIM_DIR"):
        sim.reset()
    assert (tmp_path / "outer" / "repo").is_dir()


def test_reset_refuses_a_non_empty_folder_without_state(tmp_path):
    sim_dir = tmp_path / "sim"
    sim_dir.mkdir()
    (sim_dir / "keep.txt").write_text("mine", encoding="utf-8")
    sim = _sim(tmp_path, FakeTrigger())
    with pytest.raises(ValueError, match="SIM_DIR") as e:
        sim.reset()
    assert str(tmp_path) not in str(e.value)
    assert (sim_dir / "keep.txt").is_file()


def test_reset_accepts_a_missing_or_empty_folder(tmp_path):
    _sim(tmp_path, FakeTrigger()).reset()
    assert load_state(tmp_path / "sim") == SimState()


class MixedRuns:
    """A run's state follows its id: every third run failed, every fifth unknown, the rest succeeded."""

    def __init__(self):
        self.lock, self.calls = threading.Lock(), 0

    def get(self, run_id):
        with self.lock:
            self.calls += 1
        n = sum(map(ord, run_id))
        if n % 5 == 0:
            return None
        return {"status": "failed" if n % 3 == 0 else "succeeded"}


def test_status_in_parallel_matches_one_at_a_time(tmp_path):
    t = FakeTrigger()
    one = _sim(tmp_path, t, versions=(CONTROL, INTERVENTION), runs=MixedRuns(), concurrency=1)
    one.reset()
    one.advance(weeks=3)
    runs = MixedRuns()
    many = _sim(tmp_path, t, versions=(CONTROL, INTERVENTION), runs=runs, concurrency=4)
    assert many.status() == one.status()
    assert runs.calls == len(load_state(tmp_path / "sim").sent)
    rows = many.status()
    assert sum(r.failed for r in rows) > 0 and sum(r.unknown for r in rows) > 0


def test_wait_reports_failed_and_unknown_runs_per_week(tmp_path):
    seen = []
    sim = _sim(tmp_path, FakeTrigger(), runs=MixedRuns(), sleep=lambda s: None, clock=lambda: 0.0)
    sim.reset()
    report = sim.advance(weeks=2, wait=True, week_done=lambda week, failed, unknown: seen.append(
        (week, failed, unknown)))
    assert [w for w, _, _ in seen] == [1, 2]
    assert report.failed == sum(f for _, f, _ in seen) > 0
    assert report.unknown == sum(u for _, _, u in seen) > 0


def test_advance_without_wait_reports_no_failures(tmp_path):
    sim = _sim(tmp_path, FakeTrigger(), runs=FakeRuns(status="failed"))
    sim.reset()
    report = sim.advance()
    assert report.failed == report.unknown == 0


def test_sim_data_dir_defaults_to_the_lab_data_and_ignores_data_dir(tmp_path, monkeypatch):
    from agentic_lab.config import SYNTHETIC_DIR
    from agentic_lab.sim import wiring
    monkeypatch.delenv("SIM_DATA_DIR", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "empty"))
    assert load_sim_settings(env_file=None).sim_data_dir == SYNTHETIC_DIR
    monkeypatch.setenv("SIM_DIR", str(tmp_path / "sim"))
    sim = wiring.build_simulator([CONTROL])  # schedules read from SIM_DATA_DIR, not DATA_DIR
    assert sim.status()[0].file_id == "ela9v2-a01"
    monkeypatch.setenv("SIM_DATA_DIR", "some/data")
    s = load_sim_settings(env_file=None)
    assert s.sim_data_dir.is_absolute() and s.sim_data_dir.parts[-2:] == ("some", "data")
