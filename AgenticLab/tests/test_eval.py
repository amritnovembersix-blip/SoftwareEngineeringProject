import json

import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic import dataset
from agentic_lab.labels import load_labels
from agentic_lab import eval as ev
from agentic_lab.labels import LabelRow
from agentic.models import (Finding, MetStandard, ReviewResult, SubmissionRef, Verification)


def _finding(code, p=None):
    return Finding(standard_code=code, concept="c", description="d", paragraphs=[1], reasons=["r"],
                   observation_count=1, summary="s", p_correct=p)


def _result(findings=(), met=()):
    ref = SubmissionRef(course_id="c", assignment_id="a", user_id="u", submission_id="s", attempt=1)
    return ReviewResult(submission_ref=ref, course_grade=9, strategy="full", standards=[],
                        observations=[], findings=list(findings), met_standards=list(met),
                        dropped=[], verification=Verification(status="ok"))


def test_concept_of():
    assert ev.concept_of("9-10.W.3.A.d") == "9-10.W.3.A"
    assert ev.concept_of("9-10.W.2.A") == "9-10.W.2.A"


def test_score_exact_and_concept():
    found, expected = {"9-10.W.3.A.d"}, {"9-10.W.3.A.a"}
    assert ev.score(found, expected, "exact") == ev.Counts(tp=0, fp=1, fn=1)
    assert ev.score(found, expected, "concept") == ev.Counts(tp=1, fp=0, fn=0)


def test_counts_zero_denominator():
    c = ev.Counts(tp=0, fp=0, fn=0)
    assert c.precision == 0.0 and c.recall == 0.0
    c = ev.Counts(tp=1, fp=1, fn=3)
    assert c.precision == 0.5 and c.recall == 0.25


def test_split_labels_out_of_scope():
    row = LabelRow.model_validate(label_row(meets_all_standards=False, underdeveloped=[
        {"standard": "9-10.W.1.A.a", "standard_grade": "9 to 10", "level": 1, "level_label": "x",
         "strand": "W", "concept": "c", "description": "d"},
        {"standard": "9-10.L.9.Z.z", "standard_grade": "9 to 10", "level": 1, "level_label": "x",
         "strand": "L", "concept": "c", "description": "d"}]))
    inside, outside = ev.split_labels(row, {"9-10.W.1.A.a"})
    assert inside == {"9-10.W.1.A.a"} and outside == ["9-10.L.9.Z.z"]


def test_brier_known_values():
    assert ev.brier([(1.0, True), (0.0, False)]) == 0.0
    assert ev.brier([(0.5, True)]) == 0.25
    assert ev.brier([]) is None


def test_reliability_bins():
    bins = ev.reliability([(0.0, False), (0.1, False), (0.5, True), (1.0, True)], bins=5)
    assert [b.count for b in bins] == [2, 0, 1, 0, 1]
    assert bins[0].lo == 0.0 and bins[0].hi == pytest.approx(0.2)
    assert bins[0].mean_p == pytest.approx(0.05) and bins[0].frac_true == 0.0
    assert bins[1].mean_p == 0.0 and bins[1].frac_true == 0.0  # empty bin
    assert bins[4].frac_true == 1.0 and bins[4].mean_p == 1.0  # p=1.0 in last bin


def test_threshold_table():
    per_student = [([_finding("9-10.W.1.A.a", 0.9), _finding("9-10.W.2.A", 0.2),
                     _finding("9-10.W.3.A.a", None)], {"9-10.W.1.A.b", "9-10.W.3.A.b"})]
    rows = ev.threshold_table(per_student, cutoffs=(0.1, 0.5))
    # cutoff 0.1: all kept; concepts found {1.A,2.A,3.A}, expected {1.A,3.A}: tp2 fp1 fn0
    assert (rows[0].cutoff, rows[0].precision, rows[0].recall) == (0.1, pytest.approx(2 / 3), 1.0)
    # cutoff 0.5: the 0.2 finding is hidden: tp2 fp0 fn0
    assert (rows[1].precision, rows[1].recall) == (1.0, 1.0)


def test_miss_recall():
    r1 = _result(findings=[_finding("A.1.1.1.a")],
                 met=[MetStandard(standard_code="B.1.1.1.a", concept="c", possible_miss=True),
                      MetStandard(standard_code="C.1.1.1.a", concept="c", possible_miss=False)])
    # expected: A (found), B (missed, flagged), C (missed, not flagged)
    assert ev.miss_recall([(r1, {"A.1.1.1.a", "B.1.1.1.a", "C.1.1.1.a"})]) == 0.5
    assert ev.miss_recall([(_result(findings=[_finding("A.1.1.1.a")]), {"A.1.1.1.a"})]) is None


def _fake_setup(tmp_path, monkeypatch, n=3):
    from agentic import wiring
    from agentic.config import Settings

    data = tmp_path / "data"
    data.mkdir()
    hashes = [c * 64 for c in "abcdefgh"[:n]]
    labels = [label_row(student_hash=h, grade=9) for h in hashes]
    labels[0] = label_row(student_hash=hashes[0], grade=9, meets_all_standards=False, underdeveloped=[
        {"standard": "9-10.W.3.A.a", "standard_grade": "9 to 10", "level": 1, "level_label": "x",
         "strand": "W", "concept": "c", "description": "d"},
        {"standard": "9-10.L.9.Z.z", "standard_grade": "9 to 10", "level": 1, "level_label": "x",
         "strand": "L", "concept": "c", "description": "d"}])
    write_labels(data, "1.01", labels)
    write_essays(data, "1.01", [
        essay_row(student_hash=h, paragraphs=["One.", "Two.", "Three."], submission_id=f"sub-{i}",
                  course_id=dataset.course_id("1.01"), assignment_id=dataset.assignment_id("1.01"))
        for i, h in enumerate(hashes)])
    for k, v in {"FAKE_LLM": "1", "VERIFY": "1", "DATA_DIR": str(data), "CLASSROOM": "1.01",
                 "REPORTS_DIR": str(tmp_path / "reports")}.items():
        monkeypatch.setenv(k, v)
    s = Settings.load()
    reviewers = {"full": wiring.build_reviewer(s, strategy="full"),
                 "rag": wiring.build_reviewer(s, strategy="rag")}
    allowed = {x.code for x in wiring.build_standards_source(s).at_grade(9)}
    return s, reviewers, load_labels(s.data_dir, "1.01"), dataset.load_essays(s.data_dir, "1.01"), allowed


def test_run_evaluation_both_strategies_fake(tmp_path, monkeypatch):
    _, reviewers, labels, essays, allowed = _fake_setup(tmp_path, monkeypatch)
    report = ev.run_evaluation(reviewers, labels, essays, allowed)
    assert set(report.strategies) == {"full", "rag"}
    full = report.strategies["full"]
    assert len(full.per_student) == 3
    assert full.llm_calls > 0 and full.seconds >= 0
    assert full.false_alarm_rate is not None
    assert report.out_of_scope == ["9-10.L.9.Z.z"]
    # fake finds W.1.A.a and W.2.A for everyone; the labelled W.3.A.a is missed, L.9.Z.z is not scored
    assert full.exact.tp == 0 and full.exact.fn == 1 and full.exact.fp == 6
    assert full.concept.fn == 1
    assert full.per_student[0].expected == ["9-10.W.3.A.a"]
    assert full.per_student[0].found == ["9-10.W.1.A.a", "9-10.W.2.A"]
    text = ev.format_report(report)
    assert "full" in text and "rag" in text
    assert "Per-student (full)" in text and "9-10.W.3.A.a" in text and "aaaaaaaa" in text
    assert text.count("Met reliability") == 2
    assert "Two." not in json.dumps(report.model_dump(mode="json"))


def test_students_limit(tmp_path, monkeypatch):
    _, reviewers, labels, essays, allowed = _fake_setup(tmp_path, monkeypatch)
    report = ev.run_evaluation({"full": reviewers["full"]}, labels, essays, allowed, students=2)
    assert len(report.strategies["full"].per_student) == 2


# --- not_evident counts, recall by labeled level ----------------------------------------------

from agentic_lab.eval import not_evident_counts, recall_by_level  # noqa: E402


def test_recall_by_level():
    rows = [({"A", "B"}, {"A": "Approaching", "B": "Beginning", "C": "Developing"}),
            ({"D"}, {"D": "Approaching", "E": "Approaching"})]
    assert recall_by_level(rows) == {"Approaching": 2 / 3, "Developing": 0.0, "Beginning": 1.0}


def test_recall_by_level_missing_level_is_none():
    assert recall_by_level([({"A"}, {"A": "Beginning"})])["Approaching"] is None


def test_not_evident_counts():
    assert not_evident_counts([["A", "B"], ["A"], []]) == {"A": 2, "B": 1}


def test_format_report_shows_level_recall_and_not_evident(tmp_path):
    from agentic_lab.eval import Calibration, Counts, EvalReport, StrategyReport, format_report

    sr = StrategyReport(exact=Counts(tp=1, fp=0, fn=1), concept=Counts(tp=1, fp=0, fn=1),
                        false_alarm_rate=0.5, llm_calls=2, seconds=1.0,
                        finding_calibration=Calibration(brier=None, bins=[]),
                        met_calibration=Calibration(brier=None, bins=[]), thresholds=[],
                        miss_recall=None, not_evident={"9-10.RL.2.B": 3},
                        recall_by_level={"Approaching": 0.5, "Developing": None, "Beginning": 1.0},
                        per_student=[])
    text = format_report(EvalReport(students=2, out_of_scope=[], strategies={"full": sr}))
    assert "recall (Approaching)" in text and "0.50" in text
    assert "Not evident (full): 9-10.RL.2.B x3" in text


def test_markdown_report_has_tables(tmp_path):
    from agentic_lab.eval import (Calibration, Counts, EvalReport, StrategyReport, StudentRow,
                              format_report_markdown)

    sr = StrategyReport(exact=Counts(tp=1, fp=1, fn=1), concept=Counts(tp=1, fp=0, fn=1),
                        false_alarm_rate=0.4, llm_calls=2, seconds=3.0,
                        finding_calibration=Calibration(brier=0.285, bins=[]),
                        met_calibration=Calibration(brier=None, bins=[]), thresholds=[],
                        miss_recall=None, not_evident={"9-10.W.3.A.a": 1},
                        recall_by_level={"Approaching": 0.25, "Developing": None, "Beginning": 1.0},
                        per_student=[StudentRow(student="ae665288", expected=["9-10.W.3.A.c"],
                                                found=["9-10.RL.1.D"], finding_p=[0.9],
                                                possible_misses=[], llm_calls=1, seconds=1.0)])
    md = format_report_markdown(EvalReport(students=20, strands=["W", "RL"], out_of_scope=[],
                                           strategies={"full": sr, "full-noctx": sr}))
    assert md.startswith("# Evaluation")
    assert "| Metric | full | full-noctx |" in md
    assert "| false alarms / clean student | 0.40 | 0.40 |" in md
    assert "| Student | Expected | Found | Possible misses | LLM calls |" in md
    assert "| ae665288 | 9-10.W.3.A.c | 9-10.RL.1.D | - | 1 |" in md
    assert "9-10.W.3.A.a ×1" in md
