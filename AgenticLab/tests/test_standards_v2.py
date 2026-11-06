from collections import Counter

import pytest

from agentic.standards import loader
from agentic_lab.standards_v2 import (
    SOURCE_XLSX, build_reviewable_xlsx, draft_reviewability, read_reviewability, reviewable_codes,
    write_reviewability)

SOURCE = SOURCE_XLSX
KEPT = {"R", "RL", "RI", "W", "L"}


def rows_from_corpus():
    return loader.normalize_rows(loader.read_xlsx(SOURCE))


def test_drops_rf_and_sl_strands():
    codes = {r.code for r in draft_reviewability(rows_from_corpus())}
    dropped = {r.code for r in rows_from_corpus() if r.strand_code in ("RF", "SL")}
    assert dropped and not (codes & dropped)


def test_rule_tags():
    tags = {r.code: r for r in draft_reviewability(rows_from_corpus())}
    assert tags["3.L.1.B.a"].reviewable is False and "legib" in tags["3.L.1.B.a"].reason
    assert tags["3.L.1.B.l"].reviewable is False
    assert tags["5.W.1.D.b"].reviewable is False
    assert tags["3.L.1.A.e"].reviewable is True
    assert tags["9-10.RI.1.A"].reviewable is True


def test_draft_covers_every_r_rl_ri_w_l_row():
    expected = [r for r in rows_from_corpus() if r.strand_code in KEPT]
    draft = draft_reviewability(rows_from_corpus())
    assert len(draft) == len(expected) == 462
    assert Counter(r.strand_code for r in expected)["W"] == 163


def _write(path, rows):
    write_reviewability(path, rows)


def test_csv_round_trip(tmp_path):
    p = tmp_path / "r.csv"
    rows = draft_reviewability(rows_from_corpus())
    _write(p, rows)
    assert p.read_text(encoding="utf-8").splitlines()[0] == "code,reviewable,reason"
    back = read_reviewability(p)
    assert list(back.values()) == rows
    assert reviewable_codes(p) == {r.code for r in rows if r.reviewable}


def test_build_xlsx_keeps_only_reviewable(tmp_path):
    p, out = tmp_path / "r.csv", tmp_path / "o.xlsx"
    rows = draft_reviewability(rows_from_corpus())
    _write(p, rows)
    n = build_reviewable_xlsx(SOURCE, p, out)
    got = [r.code for r in loader.normalize_rows(loader.read_xlsx(out))]
    assert set(got) == reviewable_codes(p) and n == len(got)


def test_build_refuses_csv_missing_codes(tmp_path):
    p = tmp_path / "r.csv"
    rows = draft_reviewability(rows_from_corpus())
    _write(p, rows[1:])
    with pytest.raises(ValueError, match=rows[0].code):
        build_reviewable_xlsx(SOURCE, p, tmp_path / "o.xlsx")


def test_reference_rule_matches_sub_expectations_only():
    tags = {r.code: r for r in draft_reviewability(rows_from_corpus())}
    for code in ("9-10.RL.1.B", "9-10.RI.1.B", "6.RL.1.B"):
        assert tags[code].reviewable is True, code
    assert tags["3.R.1.B.f"].reviewable is False  # "using a dictionary or a glossary..." sub-expectation


def test_read_reviewability_is_strict(tmp_path):
    path = tmp_path / "r.csv"
    path.write_text("code,reviewable,reason\n3.L.1.A.a, TRUE ,\n3.L.1.A.b,False,why\n", encoding="utf-8")
    got = read_reviewability(path)
    assert got["3.L.1.A.a"].reviewable is True and got["3.L.1.A.b"].reviewable is False
    path.write_text("code,reviewable,reason\n3.L.1.A.a,true,\n3.L.1.A.b,yes,\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"3\.L\.1\.A\.b.*|line 3"):
        read_reviewability(path)
    with pytest.raises(ValueError, match="line 3"):
        read_reviewability(path)


def test_reads_csv_with_excel_utf8_bom(tmp_path):
    path = tmp_path / "r.csv"
    path.write_text("\ufeffcode,reviewable,reason\n3.L.1.A.a,true,\n3.L.1.A.b,false,why\n", encoding="utf-8")
    got = read_reviewability(path)
    assert list(got) == ["3.L.1.A.a", "3.L.1.A.b"]
    assert got["3.L.1.A.b"].reason == "why"
    assert reviewable_codes(path) == {"3.L.1.A.a"}
