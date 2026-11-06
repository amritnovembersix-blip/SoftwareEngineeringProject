import agentic
import json
from pathlib import Path

from fakes import essay_row, label_row, write_essays, write_labels
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "demo" / "app.py")
SECRET = "Secret essay sentence alpha."


def test_demo_app_runs_in_fake_mode(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    write_labels(data, "1.01", [label_row(student_hash="a" * 64, grade=9, meets_all_standards=False)])
    write_essays(data, "1.01", [essay_row(
        student_hash="a" * 64, paragraphs=[SECRET, "Second para.", "Third para."],
        course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1")])
    for k, v in {"FAKE_LLM": "1", "VERIFY": "1", "DATA_DIR": str(data), "CLASSROOM": "1.01"}.items():
        monkeypatch.setenv(k, v)
    at = AppTest.from_file(APP, default_timeout=30).run(timeout=30)
    assert not at.exception
    at.selectbox[0].select_index(0)
    at.button[0].click()
    at.run(timeout=30)
    assert not at.exception
    assert not at.error
    assert any("P(correct)" in m.value for m in at.markdown)


def test_demo_reading_strand_checkboxes_preset_from_assignment(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    write_labels(data, "1.02", [label_row(student_hash="a" * 64, grade=9)])
    write_essays(data, "1.02", [essay_row(
        student_hash="a" * 64, paragraphs=["One.", "Two.", "Three."],
        course_id="classroom_1.02", assignment_id="asg-classroom_1.02-essay1")])
    (data / "classroom_1.02.assignment.json").write_text('{"type": "rl"}', encoding="utf-8")
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data), "CLASSROOM": "1.02"}.items():
        monkeypatch.setenv(k, v)
    at = AppTest.from_file(APP, default_timeout=30).run(timeout=30)
    boxes = {c.label: c.value for c in at.checkbox}
    assert boxes == {"Review RL (literary text)": True, "Review RI (informational text)": False,
                     "Use assignment context": True}
    next(c for c in at.checkbox if c.label.startswith("Review RI")).check()  # turn RI on too
    at.button[0].click()
    at.run(timeout=30)
    assert not at.exception and not at.error
    assert any("Strands reviewed: W, RL, RI" in c.value for c in at.caption)


def test_demo_targeted_assignment_shows_assignment_and_hides_strand_boxes(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    write_labels(data, "1.03", [label_row(student_hash="a" * 64, grade=9)])
    write_essays(data, "1.03", [essay_row(
        student_hash="a" * 64, paragraphs=["One.", "Two.", "Three."],
        course_id="classroom_1.03", assignment_id="asg-classroom_1.03-essay1")])
    (data / "classroom_1.03.assignment.json").write_text(
        '{"id": "t", "type": "argument", "title": "Later Start", "prompt": "Argue for or against.",'
        ' "targets": ["9-10.W.2.A", "9-10.W.3.A.d"]}', encoding="utf-8")
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data), "CLASSROOM": "1.03"}.items():
        monkeypatch.setenv(k, v)
    at = AppTest.from_file(APP, default_timeout=30).run(timeout=30)
    assert [c.label for c in at.checkbox] == ["Use assignment context"]
    assert any("Later Start" in e.label for e in at.expander)
    at.button[0].click()
    at.run(timeout=30)
    assert not at.exception and not at.error
    assert any("Targets: 9-10.W.2.A, 9-10.W.3.A.d" in c.value for c in at.caption)
    assert any("Not evident" in sh.value for sh in at.subheader)


def test_compare_tab_shows_saved_reports_without_review_data(tmp_path, monkeypatch):
    from lab_fakes import eval_report_file

    reports = tmp_path / "reports"
    reports.mkdir()
    eval_report_file(reports, "20261005T213816", {"full": (0.4, [("aaaa1111", [], ["9-10.RL.1.D"])]),
                                                  "full-noctx": (0.5, [("aaaa1111", [], [])])})
    eval_report_file(reports, "20261006T090000", {"full": (0.1, [("aaaa1111", [], [])])})
    empty = tmp_path / "nodata"
    empty.mkdir()
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(empty), "CLASSROOM": "9.99",
                 "REPORTS_DIR": str(reports)}.items():
        monkeypatch.setenv(k, v)
    at = AppTest.from_file(APP, default_timeout=30).run(timeout=30)
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Review", "Compare reports", "Review one", "Compare runs", "Simulation", "Class summary"]
    at.multiselect[0].select("eval-20261005T213816.json").select("eval-20261006T090000.json")
    at.run(timeout=30)
    assert not at.exception
    summary = at.dataframe[0].value
    assert list(summary.columns) == ["Metric", "10-05 21:38 · full", "10-05 21:38 · full-noctx",
                                     "10-06 09:00 · full"]
    assert any("Removed" in list(df.value.columns) for df in at.dataframe)


def test_assignment_panel_shows_focus_notes(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    write_labels(data, "1.07", [label_row(student_hash="a" * 64, grade=9)])
    write_essays(data, "1.07", [essay_row(student_hash="a" * 64, paragraphs=["One."],
                                          course_id="classroom_1.07", assignment_id="asg-classroom_1.07-essay1")])
    (data / "classroom_1.07.assignment.json").write_text(
        '{"title": "T", "prompt": "P", "targets": ["9-10.W.2.A"], "notes": {"9-10.W.2.A": "Focus on organization."}}',
        encoding="utf-8")
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data), "CLASSROOM": "1.07"}.items():
        monkeypatch.setenv(k, v)
    at = AppTest.from_file(APP, default_timeout=30).run(timeout=30)
    assert any("Focus on organization." in m.value for m in at.markdown)


def test_demo_stays_on_the_mvp_pipeline(tmp_path, monkeypatch):
    """Ruling T9a: the demo's strategy picker is an MVP tool."""
    from agentic import wiring
    from agentic.review.graph import Reviewer

    data = tmp_path / "data"
    data.mkdir()
    write_labels(data, "1.01", [label_row(student_hash="a" * 64, grade=9)])
    write_essays(data, "1.01", [essay_row(
        student_hash="a" * 64, paragraphs=["One.", "Two.", "Three."],
        course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1")])
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data), "CLASSROOM": "1.01", "REVIEW_PIPELINE": "v2"}.items():
        monkeypatch.setenv(k, v)
    built, real = [], wiring.build_reviewer
    monkeypatch.setattr(wiring, "build_reviewer", lambda *a, **k: built.append(real(*a, **k)) or built[-1])
    at = AppTest.from_file(APP, default_timeout=30).run(timeout=30)
    at.button[0].click()
    at.run(timeout=30)
    assert not at.exception and not at.error
    assert len(built) == 1 and type(built[0]) is Reviewer


def _v2_env(tmp_path, monkeypatch, with_submissions: bool = True):
    import shutil

    fixture = Path(agentic.__file__).resolve().parents[2] / "tests" / "data" / "coursedir"
    data = tmp_path / "data"
    shutil.copytree(fixture, data)
    if not with_submissions:
        shutil.rmtree(data / "courses" / "0.90-control" / "submissions")
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data), "REPORTS_DIR": str(tmp_path / "local" / "reports"),
                 "CLASSROOM": "9.99"}.items():
        monkeypatch.setenv(k, v)


def test_review_one_and_compare_runs_tabs(tmp_path, monkeypatch):
    _v2_env(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Review", "Compare reports", "Review one", "Compare runs", "Simulation", "Class summary"]
    assert any("Need two saved runs" in i.value for i in at.info)
    run = next(b for b in at.button if b.key == "one-run")
    run.click()
    at.run(timeout=60)
    run = next(b for b in at.button if b.key == "one-run")
    run.click()
    at.run(timeout=60)
    assert not at.exception
    assert not any("Review failed" in e.value for e in at.error)  # the MVP tab's own no-essays error is expected
    saved = list((tmp_path / "local" / "reviews").glob("*.json"))
    assert len(saved) == 2
    headers = [sh.value for sh in at.subheader]
    assert "Growth areas" in headers and "Verdicts by reviewer" in headers and "Root-cause links" in headers
    assert any("raw reviewer reply" in e.label for e in at.expander)
    assert not any(w in " ".join(sh.value for sh in at.subheader).lower() for w in ("weakness", "severity"))
    next(c for c in at.checkbox if c.key == "one-expected").check()  # no answer key in the fixture
    at.run(timeout=60)
    assert not at.exception
    assert any("no answer key" in i.value for i in at.info)
    assert not any("Need two saved runs" in i.value for i in at.info)  # both runs are listed now
    assert any(sh.value == "Options that differ" for sh in at.subheader)


def test_review_one_without_submissions_shows_a_message(tmp_path, monkeypatch):
    _v2_env(tmp_path, monkeypatch, with_submissions=False)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    assert not at.exception
    assert any("No submissions for this course" in i.value for i in at.info)


def test_review_one_reasoning_picker_feeds_the_options(tmp_path, monkeypatch):
    _v2_env(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    next(t for t in at.text_input if t.key == "one-reasoning").set_value("3-5-all=low, 6+-all=high")
    next(t for t in at.selectbox if t.key == "one-profile").set_value("lean")
    at.run(timeout=60)
    next(b for b in at.button if b.key == "one-run").click()
    at.run(timeout=60)
    assert not at.exception
    saved = list((tmp_path / "local" / "reviews").glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text(encoding="utf-8"))["options"]["reasoning"] == {
        "3-5-all": "low", "6+-all": "high"}


class FakeSim:
    """Stands in for the Simulator: a few statuses, and a record of the calls the tab makes."""
    instances: list = []

    def __init__(self, versions):
        self.versions, self.calls = versions, []
        FakeSim.instances.append(self)

    def status(self):
        from agentic_lab.sim.simulator import AssignmentStatus
        self.calls.append(("status",))
        return [AssignmentStatus("2.00-control", "ela9v2-a01", 1, True, 18, succeeded=18),
                AssignmentStatus("2.00-control", "ela9v2-a03", 2, True, 18, queued=3, succeeded=15),
                AssignmentStatus("2.00-control", "ela9v2-a05", 3, False, 0)]

    def advance(self, weeks=1, **kw):
        self.calls.append(("advance", weeks))

    def reset(self):
        self.calls.append(("reset",))


def _sim_env(tmp_path, monkeypatch, week: int = 2):
    from agentic import wiring
    from agentic_lab import sim_view
    from agentic_lab.sim import wiring as sim_wiring
    from agentic_lab.sim.state import SimState, save_state

    sim_dir = tmp_path / "sim"
    save_state(sim_dir, SimState(week=week))
    for k, v in {"FAKE_LLM": "1", "SIM_DIR": str(sim_dir)}.items():
        monkeypatch.setenv(k, v)
    FakeSim.instances.clear()
    monkeypatch.setattr(sim_wiring, "build_simulator", FakeSim)

    def no_database(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(wiring, "connect", no_database)
    gt_calls = []
    monkeypatch.setattr(sim_view, "ground_truth", lambda *a, **k: gt_calls.append(a) or {})
    return gt_calls


def _status_calls():
    return [c for sim in FakeSim.instances for c in sim.calls if c == ("status",)]


def _pipeline(at):
    return next(df.value for df in at.dataframe if "assignment" in df.value.columns)


def test_simulation_tab_renders_pipeline_without_a_database(tmp_path, monkeypatch):
    _sim_env(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    assert not at.exception
    assert "Simulation" in [t.label for t in at.tabs]
    assert any("Postgres is not reachable; showing the pipeline only" in w.value for w in at.warning)
    assert not at.error
    next(b for b in at.button if b.key == "sim-refresh").click()
    at.run(timeout=60)
    assert list(_pipeline(at)["week"]) == [1, 2]  # as of week 2: the week 3 row is hidden


def test_simulation_run_states_load_only_on_demand(tmp_path, monkeypatch):
    _sim_env(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    assert not at.exception and not at.error
    assert _status_calls() == []  # no run lookups until asked
    table = _pipeline(at)
    assert len(table) > 0 and table["succeeded"].isna().all()  # released/sent from the state, counts blank
    next(b for b in at.button if b.key == "sim-refresh").click()
    at.run(timeout=60)
    assert len(_status_calls()) == 1 and list(_pipeline(at)["succeeded"]) == [18, 15]
    at.run(timeout=60)  # a plain rerun reuses the cached run states
    next(t for t in at.toggle if t.key == "sim-side").set_value(True)
    at.run(timeout=60)
    assert len(_status_calls()) == 1 and list(_pipeline(at)["succeeded"]) == [18, 15]
    next(b for b in at.button if b.key == "sim-advance").click()
    at.run(timeout=60)
    assert len(_status_calls()) == 2  # refreshed after Advance
    assert len(FakeSim.instances) == 1  # built once per session


def test_simulation_reads_course_folders_from_sim_data_dir(tmp_path, monkeypatch):
    _sim_env(tmp_path, monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DATA_DIR", str(empty))  # what the agentic service reads; not the simulator's source
    monkeypatch.delenv("SIM_DATA_DIR", raising=False)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    assert not at.exception
    assert not any("SIM_DATA_DIR" in i.value for i in at.info)  # the Review one tab has its own message
    assert len(_pipeline(at)) > 0


def test_simulation_without_course_folders_names_the_setting_not_a_path(tmp_path, monkeypatch):
    _sim_env(tmp_path, monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("SIM_DATA_DIR", str(empty))
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    assert not at.exception
    info = next(i.value for i in at.info if "course folders" in i.value)
    assert "SIM_DATA_DIR" in info and str(tmp_path) not in info and "\\" not in info


def test_simulation_advance_button_calls_advance_one(tmp_path, monkeypatch):
    _sim_env(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    next(b for b in at.button if b.key == "sim-advance").click()
    at.run(timeout=60)
    assert not at.exception and not at.error
    assert ("advance", 1) in [c for sim in FakeSim.instances for c in sim.calls]


def test_simulation_ground_truth_is_off_until_toggled(tmp_path, monkeypatch):
    gt_calls = _sim_env(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    toggle = next(t for t in at.toggle if t.key == "sim-gt")
    assert toggle.value is False
    assert gt_calls == []
    toggle.set_value(True)
    at.run(timeout=60)
    assert not at.exception
    assert gt_calls == []  # no database: the grid, and with it the overlay, is skipped


def test_simulation_ground_truth_is_read_only_when_toggled_on(tmp_path, monkeypatch):
    from agentic import wiring

    gt_calls = _sim_env(tmp_path, monkeypatch)

    class EmptyConn:
        def execute(self, *a, **k):
            return []

        def close(self):
            pass

    monkeypatch.setattr(wiring, "connect", lambda *a, **k: EmptyConn())
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    assert not at.exception and not at.error
    assert gt_calls == []
    next(t for t in at.toggle if t.key == "sim-gt").set_value(True)
    at.run(timeout=60)
    assert not at.exception and not at.error
    assert {c[1] for c in gt_calls} == {"2.00-control", "2.00-intervention"}


def _grid_env(tmp_path, monkeypatch, gt=None):
    """The Simulation tab with a fake Simulator, a fake connection and a few canned FindingCells."""
    from datetime import datetime, timezone

    from agentic import wiring
    from agentic_lab import sim_view
    from agentic_lab.config import SYNTHETIC_DIR
    from agentic_lab.sim import findings_query
    from agentic_lab.sim.findings_query import FindingCell

    calls = _sim_env(tmp_path, monkeypatch, week=5)  # interventions start in week 4
    from agentic.coursedata import CourseDir

    data = SYNTHETIC_DIR
    control = CourseDir(data, "2.00-control").roster()[0].student_hash
    inter = CourseDir(data, "2.00-intervention").roster()[0].student_hash
    due = datetime(2027, 1, 14, tzinfo=timezone.utc)
    area = "9–10 · W · Argument"
    canned = {"classroom_2.00-control": [FindingCell(control, area, due, 2, "developing", 1)],
              "classroom_2.00-intervention": [FindingCell(inter, area, due, 1, "beginning", 0)]}
    monkeypatch.setattr(wiring, "connect", lambda *a, **k: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(findings_query, "findings_by_area", lambda conn, course, through: canned[course])
    if gt is not None:
        monkeypatch.setattr(sim_view, "ground_truth", lambda d, v, w, f: gt(v, control, inter))
    return control, inter, area, calls


def _grid(at):
    return next(df.value for df in at.dataframe if any("W" in str(c) for c in df.value.columns))


def test_simulation_grid_side_by_side_marks_intervention_students(tmp_path, monkeypatch):
    _grid_env(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    next(t for t in at.toggle if t.key == "sim-side").set_value(True)
    at.run(timeout=60)
    assert not at.exception and not at.error
    grid = _grid(at)
    assert "p01 ◆" in list(grid.index)
    assert grid.iloc[0, 0] == "2 ★1 | 1"


def test_simulation_grid_single_view_with_intervention_twin_picked(tmp_path, monkeypatch):
    control, inter, area, _ = _grid_env(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    assert list(_grid(at).index) == [control[:8]]  # control by default: nothing marked
    next(r for r in at.radio if r.key == "sim-class").set_value("2.00-intervention")
    at.run(timeout=60)
    assert not at.exception and not at.error
    grid = _grid(at)
    assert list(grid.index) == [f"{inter[:8]} ◆"]
    assert grid.iloc[0, 0] == "1"


def test_simulation_ground_truth_fills_cells_without_findings(tmp_path, monkeypatch):
    def gt(version, control, inter):
        return {(control, "9–10 · W · Argument"): 3, (control, "6–8 · L · Grammar"): 2}

    control, inter, area, _ = _grid_env(tmp_path, monkeypatch, gt=gt)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    next(t for t in at.toggle if t.key == "sim-gt").set_value(True)
    at.run(timeout=60)
    assert not at.exception and not at.error
    grid = _grid(at)
    assert grid.loc[control[:8], "6–8 · L · Grammar"] == "– | gt 2"
    assert grid.loc[control[:8], area] == "2 ★1 | gt 3"


def test_simulation_status_failure_warns_and_still_shows_the_pipeline(tmp_path, monkeypatch):
    _sim_env(tmp_path, monkeypatch)

    def down(self):
        raise OSError("service down")

    monkeypatch.setattr(FakeSim, "status", down)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    next(b for b in at.button if b.key == "sim-refresh").click()
    at.run(timeout=60)
    assert not at.exception
    assert any("agentic service is not reachable" in w.value for w in at.warning)
    table = next(df.value for df in at.dataframe if "assignment" in df.value.columns)
    assert len(table) > 0


def test_simulation_reset_button_calls_reset(tmp_path, monkeypatch):
    _sim_env(tmp_path, monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    next(b for b in at.button if b.key == "sim-reset").click()
    at.run(timeout=60)
    assert not at.exception and not at.error
    assert ("reset",) in [c for sim in FakeSim.instances for c in sim.calls]


def test_simulation_ground_truth_failure_shows_only_the_class_name(tmp_path, monkeypatch):
    from agentic_lab import sim_view

    _grid_env(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise FileNotFoundError("C:/secret/path/trajectory.jsonl")

    monkeypatch.setattr(sim_view, "ground_truth", boom)
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    next(t for t in at.toggle if t.key == "sim-gt").set_value(True)
    at.run(timeout=60)
    assert not at.exception
    assert [e.value for e in at.error if "Review" not in e.value and "essay" not in e.value.lower()] \
        == ["FileNotFoundError"]


def test_class_summary_tab_renders_from_the_answer_key_without_a_database(monkeypatch):
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setenv("SIM_DATA_DIR", str(Path(APP).parents[1] / "data" / "synthetic"))
    monkeypatch.setenv("CLASSROOM", "1.01")
    at = AppTest.from_file(APP, default_timeout=60).run(timeout=60)
    assert not at.exception
    assert "Class summary" in [t.label for t in at.tabs]
    next(r for r in at.radio if r.label == "Source").set_value("Answer key (no reviews needed)")
    at.run(timeout=60)
    assert not at.exception and not at.error
    assert any(h.value == "Reteach groups" for h in at.subheader)
    assert any("←" in str(df.value) for df in at.dataframe) or any("←" in m.value for m in at.markdown)
