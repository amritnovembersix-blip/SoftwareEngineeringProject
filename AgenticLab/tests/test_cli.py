import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic_lab.__main__ import main

SECRET = "Secret essay sentence alpha."


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    write_labels(data, "1.01", [label_row(student_hash="a" * 64, grade=9),
                                label_row(student_hash="ab" + "c" * 62, grade=9)])
    write_essays(data, "1.01", [
        essay_row(student_hash="a" * 64, paragraphs=[SECRET, "Second para.", "Third para."],
                  course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1",
                  submission_id="sub-1"),
        essay_row(student_hash="ab" + "c" * 62, paragraphs=["Other."], course_id="classroom_1.01",
                  assignment_id="asg-classroom_1.01-essay1", submission_id="sub-2"),
    ])
    reports = tmp_path / "reports"
    for k, v in {"FAKE_LLM": "1", "VERIFY": "1", "REVIEW_STRATEGY": "full", "DATA_DIR": str(data),
                 "REPORTS_DIR": str(reports), "CLASSROOM": "1.01"}.items():
        monkeypatch.setenv(k, v)
    return reports


def test_evaluate_writes_report_and_prints(cli_env, capsys):
    assert main(["evaluate", "--strategy", "full", "--students", "1"]) == 0
    out = capsys.readouterr().out
    assert "full" in out and SECRET not in out
    files = list(cli_env.glob("eval-*.json"))
    assert len(files) == 1 and SECRET not in files[0].read_text(encoding="utf-8")


def test_evaluate_bad_students_is_setup_error(cli_env, capsys):
    assert main(["evaluate", "--students", "0"]) == 2
    assert capsys.readouterr().err.strip()


def test_evaluate_defaults_to_both_strategies(cli_env, capsys):
    assert main(["evaluate"]) == 0
    out = capsys.readouterr().out
    assert "full" in out and "rag" in out
    body = next(cli_env.glob("eval-*.json")).read_text(encoding="utf-8")
    assert '"full"' in body and '"rag"' in body


def test_evaluate_uses_classroom_assignment_strands(cli_env, capsys, tmp_path):
    import json

    (tmp_path / "data" / "classroom_1.01.assignment.json").write_text('{"type": "ri"}', encoding="utf-8")
    assert main(["evaluate", "--strategy", "full"]) == 0
    out = capsys.readouterr().out
    assert "Strands reviewed: W, RI" in out
    report = next(cli_env.glob("eval-*.json"))
    assert json.loads(report.read_text(encoding="utf-8"))["strands"] == ["W", "RI"]


def test_evaluate_compare_context_runs_both(cli_env, capsys):
    import json

    assert main(["evaluate", "--strategy", "full", "--compare-context"]) == 0
    report = json.loads(next(cli_env.glob("eval-*.json")).read_text(encoding="utf-8"))
    assert set(report["strategies"]) == {"full", "full-noctx"}
    assert "full-noctx" in capsys.readouterr().out


def test_evaluate_writes_markdown_report_next_to_json(cli_env, capsys):
    assert main(["evaluate", "--strategy", "full"]) == 0
    md = next(cli_env.glob("eval-*.md"))
    assert next(cli_env.glob("eval-*.json")).stem == md.stem
    assert md.read_text(encoding="utf-8").startswith("# Evaluation")
    assert str(md) in capsys.readouterr().out


def test_evaluate_stays_on_the_mvp_pipeline(cli_env, capsys, monkeypatch):
    """Ruling T9a: strategy evaluation compares MVP strategies until its own v2 task."""
    from agentic import wiring
    from agentic.review.graph import Reviewer

    monkeypatch.setenv("REVIEW_PIPELINE", "v2")
    built, real = [], wiring.build_reviewer
    monkeypatch.setattr(wiring, "build_reviewer", lambda *a, **k: built.append(real(*a, **k)) or built[-1])
    assert main(["evaluate", "--strategy", "both", "--students", "1"]) == 0
    assert len(built) == 2 and all(type(r) is Reviewer for r in built)


class _ProviderError(Exception):
    """Shaped like google.genai's APIError: an HTTP code and a status, plus a message that may quote text."""

    def __init__(self, code, status, message):
        super().__init__(message)
        self.code, self.status = code, status


def test_failure_label_adds_http_status_but_never_the_message():
    from agentic_lab.__main__ import _failure
    label = _failure(_ProviderError(402, "RESOURCE_EXHAUSTED", f"quota; {SECRET}"))
    assert label == "_ProviderError 402 RESOURCE_EXHAUSTED"
    assert SECRET not in label


def test_failure_label_is_class_only_without_a_status():
    from agentic_lab.__main__ import _failure
    assert _failure(ValueError(SECRET)) == "ValueError"
    assert _failure(_ProviderError(None, SECRET, SECRET)) == "_ProviderError"  # a non-status string is dropped


def test_generate_course_failure_prints_http_status(tmp_path, monkeypatch, capsys):
    import agentic_lab.__main__ as cli

    def boom(*a, **k):
        raise _ProviderError(402, "RESOURCE_EXHAUSTED", SECRET)

    monkeypatch.setattr(cli, "generate_course", boom)
    monkeypatch.setattr(cli, "build_synth_chat", lambda s: object())
    monkeypatch.setenv("FAKE_LLM", "1")
    assert main(["generate-course", "--arm", "control", "--workers", "1"]) == 1
    err = capsys.readouterr().err
    assert "generate-course failed: _ProviderError 402 RESOURCE_EXHAUSTED" in err
    assert SECRET not in err


# --- simulate ---

class _FakeSim:
    instances = []

    failed = unknown = 0  # with wait: the runs that finished failed / unknown in week 4

    def __init__(self, versions=("2.00-control", "2.00-intervention"), error=None):
        self.versions, self.error, self.calls = list(versions), error, []
        _FakeSim.instances.append(self)

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def reset(self):
        self.calls.append(("reset",))

    def advance(self, weeks=1, wait=False, progress=None, week_done=None):
        from datetime import datetime, timezone
        from agentic_lab.sim.schedule import ScheduledAssignment
        from agentic_lab.sim.simulator import AdvanceReport
        self.calls.append(("advance", weeks, wait))
        self._maybe_fail()
        if progress:
            now = datetime.now(timezone.utc)
            item = ScheduledAssignment(file_id="ela9v2-a07", lms_id="ela9v2-a07@2.00-control",
                                       course_id="classroom_2.00-control", version="2.00-control", week=4,
                                       due_at=now, event_time=now)
            progress(4, item, 18, 18)
        if wait and week_done:
            week_done(4, self.failed, self.unknown)
        failed, unknown = (self.failed, self.unknown) if wait else (0, 0)
        return AdvanceReport(week=4, released=18, sent=18, already_sent=0, failed=failed, unknown=unknown)

    def run_through(self, week, wait=False, progress=None, week_done=None):
        self.calls.append(("run_through", week, wait))
        return self.advance(1, wait, progress, week_done)

    def status(self):
        from agentic_lab.sim.simulator import AssignmentStatus
        return [AssignmentStatus(version="2.00-control", file_id="ela9v2-a07", week=4, released=True, sent=18,
                                 succeeded=17, failed=1)]


@pytest.fixture
def fake_sim(monkeypatch):
    import agentic_lab.__main__ as cli
    _FakeSim.instances = []
    _FakeSim.failed = _FakeSim.unknown = 0
    err = {"e": None}
    monkeypatch.setattr(cli, "_simulator", lambda args: _FakeSim(error=err["e"]))
    return err


def test_simulate_advance_prints_a_line_per_assignment(fake_sim, capsys):
    assert main(["simulate", "advance", "--weeks", "2", "--wait"]) == 0
    out = capsys.readouterr().out
    assert "week 4  ela9v2-a07@2.00-control  released 18  sent 18" in out
    assert "week 4" in out.splitlines()[-1]
    assert _FakeSim.instances[0].calls == [("advance", 2, True)]


def test_simulate_run_through_week(fake_sim, capsys):
    assert main(["simulate", "run", "--through-week", "4"]) == 0
    assert _FakeSim.instances[0].calls[0] == ("run_through", 4, False)


def test_simulate_status_prints_a_table(fake_sim, capsys):
    assert main(["simulate", "status"]) == 0
    out = capsys.readouterr().out
    assert "ela9v2-a07@2.00-control" in out and "yes" in out and "17" in out


def test_simulate_reset_reviews_calls_reset_once_per_course(fake_sim, monkeypatch, capsys):
    import agentic.results.reset as reset_mod
    import agentic_lab.__main__ as cli
    calls, closed = [], []

    class Conn:
        def close(self):
            closed.append(1)

    monkeypatch.setattr(cli.wiring, "connect", lambda s: Conn())
    monkeypatch.setattr(reset_mod, "reset_reviews", lambda conn, cid: calls.append(cid) or {})
    assert main(["simulate", "reset", "--reviews"]) == 0
    assert calls == ["classroom_2.00-control", "classroom_2.00-intervention"]
    assert closed == [1]
    assert ("reset",) in _FakeSim.instances[0].calls


def test_simulate_reset_without_reviews_touches_no_database(fake_sim, monkeypatch, capsys):
    import agentic_lab.__main__ as cli
    monkeypatch.setattr(cli.wiring, "connect", lambda s: pytest.fail("no db"))
    assert main(["simulate", "reset"]) == 0
    assert capsys.readouterr().out.strip()


def test_simulate_trigger_error_exits_2_without_paths(fake_sim, capsys):
    from agentic_lab.sim.trigger import TriggerError
    fake_sim["e"] = TriggerError("sub-1 of ela9v2-a07@2.00-control: HTTP 401")
    assert main(["simulate", "advance"]) == 2
    err = capsys.readouterr().err
    assert "HTTP 401" in err and "\\" not in err and "/Users" not in err


def test_simulate_other_error_prints_class_name_only(fake_sim, capsys):
    fake_sim["e"] = ValueError(SECRET)
    assert main(["simulate", "advance"]) == 1
    err = capsys.readouterr().err
    assert "ValueError" in err and SECRET not in err


def _capture_versions(monkeypatch):
    from agentic_lab.sim import wiring as sim_wiring
    seen = {}

    class Sim(_FakeSim):
        def __init__(self, data_dir, versions, *a, **k):
            super().__init__(versions)
            seen["versions"] = list(versions)

    monkeypatch.setattr(sim_wiring, "Simulator", Sim)
    return seen


def test_simulate_course_goes_after_the_command(monkeypatch, capsys):
    seen = _capture_versions(monkeypatch)
    assert main(["simulate", "status", "--course", "2.00-control"]) == 0
    assert seen["versions"] == ["2.00-control"]


def test_simulate_course_defaults_to_both_in_order(monkeypatch, capsys):
    seen = _capture_versions(monkeypatch)
    assert main(["simulate", "advance"]) == 0
    assert seen["versions"] == ["2.00-control", "2.00-intervention"]
    assert main(["simulate", "status", "--course", "both"]) == 0
    assert seen["versions"] == ["2.00-control", "2.00-intervention"]


def test_simulate_reset_reviews_says_to_restart_the_service(fake_sim, monkeypatch, capsys):
    import agentic.results.reset as reset_mod
    import agentic_lab.__main__ as cli
    monkeypatch.setattr(cli.wiring, "connect", lambda s: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(reset_mod, "reset_reviews", lambda conn, cid: {})
    assert main(["simulate", "reset", "--reviews"]) == 0
    assert "restart the agentic service before replaying" in capsys.readouterr().out


def test_simulate_wait_prints_failed_and_unknown_per_week(fake_sim, capsys):
    assert main(["simulate", "advance", "--wait"]) == 0
    assert "week 4  failed 0  unknown 0" in capsys.readouterr().out


def test_simulate_wait_with_failed_runs_exits_3(fake_sim, capsys):
    _FakeSim.failed, _FakeSim.unknown = 2, 1
    assert main(["simulate", "run", "--through-week", "4", "--wait"]) == 3
    out = capsys.readouterr().out
    assert "week 4  failed 2  unknown 1" in out


def test_simulate_unknown_runs_alone_exit_0(fake_sim, capsys):
    _FakeSim.unknown = 3
    assert main(["simulate", "advance", "--wait"]) == 0


def test_simulate_wait_timeout_prints_the_message_and_exits_2(fake_sim, capsys):
    fake_sim["e"] = TimeoutError("week 4: 3 of 36 runs not finished after 30 minutes")
    assert main(["simulate", "advance", "--wait"]) == 2
    assert "week 4: 3 of 36 runs not finished after 30 minutes" in capsys.readouterr().err
