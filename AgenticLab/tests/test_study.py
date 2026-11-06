import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentic_lab.__main__ import main
from agentic_lab.analytics_lab import results_from_answer_key
from agentic_lab.study import sign_test_p, study, wilcoxon_p

DATA = Path(__file__).resolve().parents[1] / "data" / "synthetic"
CONTROL, INTERVENTION = "2.00-control", "2.00-intervention"
END = datetime(2100, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def report():
    readers = {v: results_from_answer_key(DATA, v) for v in (CONTROL, INTERVENTION)}
    return study(readers, DATA, twins="2.00")


def _iv(report, iv_id):
    return next(i for i in report.interventions if i.id == iv_id)


def test_sign_test_three_of_three_is_a_quarter():
    assert sign_test_p([-1.0, -0.5, -2.0]) == 0.25


def test_sign_test_drops_zeros_and_is_one_when_empty():
    assert sign_test_p([0.0, -1.0, 1.0, 0.0]) == 1.0
    assert sign_test_p([0.0]) == 1.0


def test_wilcoxon_is_none_when_every_difference_is_zero():
    assert wilcoxon_p([0.0] * 6) is None


def test_wilcoxon_is_none_below_five_pairs():
    assert wilcoxon_p([-1.0, -2.0, -0.5, -1.5]) is None
    assert 0 <= wilcoxon_p([-1.0, -2.0, -0.5, -1.5, -3.0]) <= 1


def test_results_are_growth_areas_and_meets_with_no_confidence_or_links():
    reader = results_from_answer_key(DATA, INTERVENTION)
    rows = reader.verdict_rows("classroom_2.00-intervention", END)
    assert rows and {r.confidence for r in rows} == {None}
    assert {r.verdict for r in rows} == {"meets", "growth_area"}
    assert not any(r.overruled or r.notable_strength or r.finding_condition for r in rows)
    expected = list((DATA / "courses" / INTERVENTION / "answer_key" / "expected").glob("*.jsonl"))
    assert len({r.submission_id for r in rows if r.submission_type}) >= len(expected)
    assert reader.link_rows("classroom_2.00-intervention", END) == []


def test_individual_intervention_responders_improve_and_non_responder_is_flat(report):
    iv2 = _iv(report, "iv2")
    assert iv2.n_pairs == 3
    assert iv2.median_difference < 0
    assert iv2.sign_p == 0.25
    assert iv2.wilcoxon_p is None
    assert iv2.non_responder is not None
    assert abs(iv2.non_responder["difference"]) < 0.25


def test_class_lesson_improves_and_has_a_wilcoxon(report):
    iv1 = _iv(report, "iv1")
    # most of the class has no sentence-boundary growth area, so the median pair is flat; the nonzero pairs improve
    assert iv1.n_pairs >= 5 and iv1.median_difference <= 0
    assert max(iv1.differences) <= 0 and min(iv1.differences) < 0
    assert iv1.sign_p < 0.05 and iv1.wilcoxon_p is not None


def test_planted_and_measured_effects_match_exactly(report):
    for iv in report.interventions:
        assert iv.planted_median_difference == iv.median_difference
        assert iv.planted_differences == iv.differences
        if iv.non_responder:
            assert iv.non_responder["planted_difference"] == iv.non_responder["difference"]


def test_cli_writes_reports_with_codes_and_counts_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    assert main(["study", "--twins", "2.00", "--source", "answer-key"]) == 0
    out = capsys.readouterr().out
    assert "iv2" in out
    files = sorted(tmp_path.glob("study-*"))
    assert [f.suffix for f in files] == [".json", ".md"]
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert {i["id"] for i in data["interventions"]} == {"iv1", "iv2", "iv3"}
