from pathlib import Path

from agentic.config import PACKAGE_ROOT
from agentic.llm.fake import FakeEmbedder
from agentic.models import StandardRef
from agentic.review.v2.scope import BandScope, progressions
from agentic.standards.fake import InMemoryStandards, in_memory_v2_standards
from agentic.standards.strand_filter import StrandFilter

V2_XLSX = PACKAGE_ROOT / "standards" / "mls-ela-reviewable-v2.xlsx"


def _std(code, lo, hi, text="t"):
    return StandardRef(code=code, concept="c", description=text, grade_min=lo, grade_max=hi,
                       sub_expectation=text)


def _src(*refs):
    return InMemoryStandards(list(refs), FakeEmbedder())


def test_band_scope_keeps_the_band_and_strands():
    src = _src(_std("3.L.1.A.a", 3, 3), _std("5.L.1.A.a", 5, 5), _std("4.W.1.A.a", 4, 4),
               _std("6.L.1.A.a", 6, 6), _std("9-10.L.1.A", 9, 10))
    assert [s.code for s in BandScope(src, 3, 5, ["L"]).standards()] == ["3.L.1.A.a", "5.L.1.A.a"]


def test_band_scope_straddling_band_is_excluded():
    src = _src(_std("9-10.W.1.A", 9, 10))
    assert BandScope(src, 6, 9, ["W"]).standards() == []
    assert len(BandScope(src, 9, 10, ["W"]).standards()) == 1


def test_strand_filter_passes_in_range_through():
    src = _src(_std("3.L.1.A.a", 3, 3), _std("3.W.1.A.a", 3, 3))
    assert len(StrandFilter(src, ["L"]).in_range(3, 5)) == 2


def test_progression_over_three_grades():
    refs = [_std("7.RI.1.A", 7, 7, "seven"), _std("8.RI.1.A", 8, 8, "eight"),
            _std("9-10.RI.1.A", 9, 10, "nine")]
    (p,) = progressions(refs)
    assert p.group == "RI.1.A"
    assert [g for g, _ in p.steps] == [7, 8, 9]


def test_identical_text_merges_into_the_lowest_grade():
    refs = [_std("7.RI.1.A", 7, 7, "Cite  evidence"), _std("8.RI.1.A", 8, 8, "cite evidence"),
            _std("9-10.RI.1.A", 9, 10, "analyze")]
    (p,) = progressions(refs)
    assert [g for g, _ in p.steps] == [7, 9]
    assert [r.code for r in p.steps[0][1]] == ["7.RI.1.A", "8.RI.1.A"]


def test_band_and_single_grade_codes_share_a_group():
    refs = [_std("6.W.2.A.a", 6, 6, "a"), _std("6.W.2.A.b", 6, 6, "b"), _std("6.W.2.A.c", 6, 6, "c"),
            _std("9-10.W.2.A", 9, 10, "d")]
    (p,) = progressions(refs)
    assert p.group == "W.2.A"
    assert [g for g, _ in p.steps] == [6, 9]
    assert len(p.steps[0][1]) == 3


def test_in_memory_v2_standards_reads_the_catalog():
    src = in_memory_v2_standards(Path(V2_XLSX))
    codes = [s.code for s in BandScope(src, 3, 5, ["L", "R", "W"]).standards()]
    assert codes and all(c.split(".")[0] in ("3", "4", "5") for c in codes)
    assert len(src.in_range(3, 12)) == 432
    assert all(s.strand for s in src.in_range(3, 12))
