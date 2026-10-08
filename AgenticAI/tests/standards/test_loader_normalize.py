import pytest
from fakes import std_row, xlsx_fixture

from agentic.standards.loader import StandardRow, embed_text, grade_range, normalize_rows, read_xlsx


def test_grade_range_labels():
    assert grade_range("3") == (3, 3)
    assert grade_range("9 to 10") == (9, 10)
    assert grade_range("11 to 12") == (11, 12)


@pytest.mark.parametrize("label", ["", "K", "9-10", "10 to 9", "9 to", "x to y"])
def test_grade_range_rejects_unknown(label):
    with pytest.raises(ValueError):
        grade_range(label)


def test_normalize_strips_nbsp_and_merges_speaking_listening(tmp_path):
    raw = read_xlsx(xlsx_fixture(tmp_path, [
        std_row("9-10.SL.1.A.a", strand_code="SL", strand="Speaking/Listening",
                description="\xa0\xa0 Listen well. ", sub="\xa0\xa0\xa0 sub text"),
        std_row("3.W.1.A.a", grade=3),
    ]))
    rows = normalize_rows(raw)
    assert rows[0].strand == "Speaking and Listening"
    assert rows[0].description == "Listen well."
    assert rows[0].sub_expectation == "sub text"
    assert rows[1].grade == "3" and (rows[1].grade_min, rows[1].grade_max) == (3, 3)
    assert rows[1].sub_expectation is None


def test_normalize_skips_rows_without_code(tmp_path):
    raw = read_xlsx(xlsx_fixture(tmp_path, [std_row("9-10.W.1.A.a"), std_row(code=None), std_row(code=" ")]))
    assert [r.code for r in normalize_rows(raw)] == ["9-10.W.1.A.a"]


def test_embed_text_format():
    row = StandardRow("c", "9 to 10", 9, 10, "W", "Writing", "b", "Research", "Do it.", None)
    assert embed_text(row) == "Writing · Research: Do it."
    row2 = StandardRow("c", "9 to 10", 9, 10, "W", "Writing", "b", "Research", "Do it.", "more")
    assert embed_text(row2) == "Writing · Research: Do it. more"
