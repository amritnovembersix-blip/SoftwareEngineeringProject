from fakes import label_row

from agentic_lab.labels import LabelRow
from agentic_lab.demo_view import Comparison, compare, comparison_lines, student_options


def test_compare_concept_level():
    found = {"9.W.1.A.a", "9.W.2.B.a", "9.L.1.A.a"}
    expected = {"9.W.1.A.b", "9.W.3.A.a"}
    c = compare(found, expected)
    assert c == Comparison(hits=["9.W.1.A"], misses=["9.W.3.A"], extras=["9.L.1.A", "9.W.2.B"])


def test_student_options_labels():
    rows = [LabelRow(**label_row(student_hash="a" * 64, meets_all_standards=True)),
            LabelRow(**label_row(student_hash="b" * 64, meets_all_standards=False))]
    assert student_options(rows) == [("a" * 64, "aaaaaaaa · meets all"),
                                     ("b" * 64, "bbbbbbbb · has weaknesses")]


def test_comparison_lines_keep_markers():
    cmp = Comparison(hits=["9-10.W.2.A"], misses=["9-10.W.3.A"], extras=["9-10.W.1.A"])
    assert comparison_lines(cmp) == ["✓ 9-10.W.2.A", "✗ 9-10.W.3.A", "+ 9-10.W.1.A"]


def test_comparison_lines_empty():
    assert comparison_lines(Comparison([], [], [])) == ["Nothing expected, nothing found."]


# --- Compare reports tab helpers --------------------------------------------------------------

from lab_fakes import eval_report_file  # noqa: E402

from agentic_lab.demo_view import (  # noqa: E402
    calibration_rows, list_eval_reports, load_runs, not_evident_rows, student_diff, summary_rows,
)


def _two_reports(tmp_path):
    a = eval_report_file(tmp_path, "20261005T213816", {
        "full": (0.4, [("aaaa1111", [], ["9-10.RL.1.D"]), ("bbbb2222", ["9-10.W.2.A"], ["9-10.W.2.A"])]),
        "full-noctx": (0.5, [("aaaa1111", [], ["9-10.RL.1.D", "9-10.W.3.A.a"]), ("bbbb2222", ["9-10.W.2.A"], [])]),
    })
    b = eval_report_file(tmp_path, "20261006T090000", {
        "full": (0.1, [("aaaa1111", [], []), ("bbbb2222", ["9-10.W.2.A"], ["9-10.W.2.A"])]),
    })
    (tmp_path / "eval-20261001T000000.json").write_text("{not json", encoding="utf-8")
    return a, b


def test_list_eval_reports_newest_first_skips_markdown(tmp_path):
    a, b = _two_reports(tmp_path)
    (tmp_path / "eval-20261006T090000.md").write_text("# x", encoding="utf-8")
    assert [p.name for p in list_eval_reports(tmp_path)][:2] == [b.name, a.name]


def test_load_runs_labels_columns_and_skips_unreadable(tmp_path):
    a, b = _two_reports(tmp_path)
    runs, skipped = load_runs([a, b, tmp_path / "eval-20261001T000000.json"])
    assert list(runs) == ["10-05 21:38 · full", "10-05 21:38 · full-noctx", "10-06 09:00 · full"]
    assert skipped == ["eval-20261001T000000.json"]


def test_summary_rows_one_column_per_run(tmp_path):
    runs, _ = load_runs(list(_two_reports(tmp_path)))
    row = next(r for r in summary_rows(runs) if r["Metric"] == "false alarms / clean student")
    assert row == {"Metric": "false alarms / clean student", "10-05 21:38 · full": "0.40",
                   "10-05 21:38 · full-noctx": "0.50", "10-06 09:00 · full": "0.10"}


def test_student_diff_shows_added_and_removed(tmp_path):
    runs, _ = load_runs(list(_two_reports(tmp_path)))
    rows = student_diff(runs["10-05 21:38 · full"], runs["10-06 09:00 · full"])
    clean = next(r for r in rows if r["Student"] == "aaaa1111")
    assert clean["Clean"] and clean["Removed"] == "9-10.RL.1.D" and clean["Added"] == ""


def test_calibration_and_not_evident_rows(tmp_path):
    runs, _ = load_runs(list(_two_reports(tmp_path)))
    cal = calibration_rows(runs)
    assert cal == [{"Bin": "0.8–1.0", "10-05 21:38 · full": 0.5, "10-05 21:38 · full-noctx": 0.5,
                    "10-06 09:00 · full": 0.5}]
    assert not_evident_rows(runs)[0]["Standard"] == "9-10.W.3.A.a"
