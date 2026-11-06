from datetime import date
from pathlib import Path

import pytest
from agentic.analytics.status import ConceptStatus
from agentic.analytics.summary import ClassWeek, Gap, PairSuggestion, ReteachGroup

from agentic_lab import summary_view as sv

DATA = Path(__file__).resolve().parents[1] / "data" / "synthetic"
CONTROL, INTERVENTION = "2.00-control", "2.00-intervention"
H1, H2 = "a" * 64, "b" * 64


def _week(concept, monday, counts, too_little=0):
    return ClassWeek("c", concept, monday, counts, too_little)


WEEKS = [
    _week("7.W.1.A", date(2027, 1, 11), (1, 1, 0, 0), 2),   # mean 0.5
    _week("7.W.1.A", date(2027, 1, 18), (0, 0, 0, 0), 3),   # no level: None
    _week("7.W.1.B", date(2027, 1, 11), (0, 0, 2, 0)),      # 2.0
    _week("3.L.1.A", date(2027, 1, 11), (1, 0, 0, 1)),      # 1.5
    _week("4.L.1.A", date(2027, 1, 11), (0, 0, 0, 2)),      # 3.0; band 3-5 with 3.L.1.A
    _week("9-10.W.3.A", date(2027, 1, 11), (0, 2, 0, 0)),   # 1.0
]


def test_chain_strings_are_exact_and_use_short_hashes():
    groups = [ReteachGroup("3.L.1.A", "9-10.W.3.A", tuple(f"{i:0<64d}" for i in range(7))),
              ReteachGroup("3.L.1.B", "9-10.W.2.A", (H1, H2))]
    rows = sv.group_rows(groups)
    assert rows[0]["chain"] == "3.L.1.A ← 9-10.W.3.A: 7 students"
    assert rows[0]["students"] == ", ".join(f"{i:0<8d}" for i in range(7))
    assert rows[1]["students"] == "aaaaaaaa, bbbbbbbb"


def test_status_counts_in_fixed_order():
    sts = [ConceptStatus(H1, "c", "7.W.1.A", "secure", 0, "3/4", (0, 0, 0), "steady"),
           ConceptStatus(H2, "c", "7.W.1.A", "needs_attention", 2, "2/3", (2, 2), "steady"),
           ConceptStatus(H2, "c", "7.W.1.B", "needs_attention", 3, "2/3", (3, 3), "steady")]
    assert sv.status_rows(sts) == [{"status": "needs_attention", "students": 2}, {"status": "emerging", "students": 0},
                                   {"status": "secure", "students": 1}, {"status": "too_little_evidence", "students": 0}]


def test_gap_and_pair_rows():
    gaps = [Gap("7.W.1.A", (H1, H2), (2, 3), "run_on")]
    assert sv.gap_rows(gaps) == [{"concept": "7.W.1.A", "students": 2, "top_condition": "run_on",
                                  "who": "aaaaaaaa, bbbbbbbb"}]
    assert sv.pair_rows([PairSuggestion("7.W.1.A", H1, H2)]) == [
        {"concept": "7.W.1.A", "tutor": "aaaaaaaa", "tutee": "bbbbbbbb"}]


def test_heatmap_is_mean_level_per_concept_and_week():
    rows, weeks, values = sv.heatmap(WEEKS)
    assert weeks == [date(2027, 1, 11), date(2027, 1, 18)]
    assert rows == ["3.L.1.A", "4.L.1.A", "7.W.1.A", "7.W.1.B", "9-10.W.3.A"]
    assert values[("7.W.1.A", weeks[0])] == 0.5   # too_little is excluded from the mean
    assert values[("7.W.1.A", weeks[1])] is None  # no student with a level
    assert values[("7.W.1.B", weeks[1])] is None  # no row for that week
    assert values[("3.L.1.A", weeks[0])] == 1.5


def test_heatmap_grouped_by_strand_and_band_pools_students():
    rows, weeks, values = sv.heatmap(WEEKS, group=True)
    assert rows == ["3-5.L", "7.W", "9-10.W"]
    assert values[("3-5.L", weeks[0])] == pytest.approx(9 / 4)  # levels 0,3,3,3 over 4 students
    assert values[("7.W", weeks[0])] == pytest.approx(5 / 4)  # levels 0,1 and 2,2
    assert values[("7.W", weeks[1])] is None
    assert values[("9-10.W", weeks[0])] == 1.0


def test_band_of():
    assert [sv.band("3.L.1.A"), sv.band("5.W.2.A"), sv.band("6.RL.1.A"), sv.band("9-10.W.3.A")] == [
        "3-5", "3-5", "6", "9-10"]


def test_postgres_rows_build_the_same_summary():
    s = sv.summary_from_rows(
        [(H1, "c", "7.W.1.A", "needs_attention", 2, "2/3", [2, 2], "steady"),
         (H2, "c", "7.W.1.A", "needs_attention", 3, "2/3", [3, 3], "steady")],
        [("7.W.1.A", date(2027, 1, 11), 0, 0, 1, 1, 0)],
        [(1, "3.L.1.A", "7.W.1.A", H1), (1, "3.L.1.A", "7.W.1.A", H2)],
        [("7.W.1.A", H1, H2)], classroom_id="c")
    assert s.statuses[0].points == (2, 2) and s.statuses[0].status == "needs_attention"
    assert s.weeks == [ClassWeek("c", "7.W.1.A", date(2027, 1, 11), (0, 0, 1, 1), 0)]
    assert s.groups == [ReteachGroup("3.L.1.A", "7.W.1.A", (H1, H2))]
    assert s.pairs == [PairSuggestion("7.W.1.A", H1, H2)]
    assert s.gaps[0].concept_code == "7.W.1.A" and s.gaps[0].students == (H1, H2)


@pytest.fixture(scope="module")
def answer_key_summary():
    return sv.answer_key_summary(DATA, INTERVENTION)


def test_answer_key_summary_runs_with_no_database(answer_key_summary):
    s = answer_key_summary
    assert s.statuses and s.weeks
    assert all(len(st.student_id) == 64 for st in s.statuses)


def test_divergence_has_one_series_per_classroom():
    d = sv.divergence(DATA, "iv1")
    assert set(d["series"]) == {CONTROL, INTERVENTION}
    for pts in d["series"].values():
        assert pts and all(isinstance(w, int) and 0 <= m <= 3 for w, m in pts)
    assert d["marks"] == [{"week": 4, "id": "iv1"}, {"week": 4, "id": "iv2"}, {"week": 8, "id": "iv3"}]
    rows = sv.divergence_rows(d)
    assert {r["classroom"] for r in rows} == {CONTROL, INTERVENTION}


def test_intervention_ids_come_from_interventions_json():
    assert sv.intervention_ids(DATA, INTERVENTION) == ["iv1", "iv2", "iv3"]
