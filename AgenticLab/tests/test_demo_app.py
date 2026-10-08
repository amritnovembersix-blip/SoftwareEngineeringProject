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
    assert [t.label for t in at.tabs] == ["Review", "Compare reports"]
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
