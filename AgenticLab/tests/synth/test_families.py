import json
from pathlib import Path

import pytest

from agentic_lab.standards_v2 import REVIEWABILITY_CSV, reviewable_codes
from agentic_lab.synth.families import (
    SkillFamily, combine, family_levels, grade_key, load_families)

FAMILIES_JSON = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "skill_families.json"


def _fam(*codes):
    return SkillFamily("f", list(codes))


def _per_grade():
    return _fam("3.L.1.A.e", "4.L.1.A.e", "5.L.1.A.c", "6.W.3.A.c", "7.W.3.A.c",
                "8.W.3.A.c", "9-10.W.3.A.c", "11-12.W.3.A.c")


def _levels_by_grade(levels):
    return [lv for _, lv in sorted(levels.items(), key=lambda kv: grade_key(kv[0]))]


def test_grade_key():
    assert grade_key("3.L.1.A.e") == 3
    assert grade_key("9-10.W.3.A.c") == 9
    assert grade_key("11-12.W.3.A.c") == 11


def test_spec_example():
    assert _levels_by_grade(family_levels(_per_grade(), 2, 3, 9)) == [1, 1, 1, 2, 2, 3, 3]


def test_course_band_excludes_11_12():
    assert not any(c.startswith("11-12") for c in family_levels(_per_grade(), 2, 3, 9))


def test_secure_through_8_only_9_10():
    assert family_levels(_per_grade(), 8, 2, 9) == {"9-10.W.3.A.c": 2}


def test_nothing_above_secure_returns_empty():
    assert family_levels(_fam("3.L.1.A.e"), 8, 3, 9) == {}


def test_codes_at_same_grade_share_level():
    fam = _fam("3.L.1.A.e", "3.L.1.A.g", "4.L.1.A.e")
    lv = family_levels(fam, 2, 2, 9)
    assert lv["3.L.1.A.e"] == lv["3.L.1.A.g"] == 1
    assert lv["4.L.1.A.e"] == 2


def test_combine_takes_max():
    assert combine({"a": 1, "b": 3}, {"a": 2}, {"c": 1}) == {"a": 2, "b": 3, "c": 1}


def test_load_families_and_committed_file_is_reviewable():
    fams = load_families(FAMILIES_JSON)
    ok = reviewable_codes(REVIEWABILITY_CSV)
    assert fams
    for name, fam in fams.items():
        assert fam.name == name
        assert fam.codes
        assert set(fam.codes) <= ok, name


def test_load_families_shape(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"x": ["3.L.1.A.e"]}), encoding="utf-8")
    assert load_families(p)["x"].codes == ["3.L.1.A.e"]
