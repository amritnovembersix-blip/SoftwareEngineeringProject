"""Skill families and the layering rule for synthetic students (lab only; production never reads these).

A family is an ordered list of reviewable standards across grades that express one skill. It is a
data-generation construct, not a crosswalk. See the synthetic-data v2 design spec, sections 4.1 and 4.2.
"""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillFamily:
    name: str
    codes: list[str]


def load_families(path) -> dict[str, SkillFamily]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: SkillFamily(name, list(codes)) for name, codes in data.items()}


def grade_key(code: str) -> int:
    """First grade of the code's band: '3.L.1.A.e' -> 3, '9-10.W.3.A.c' -> 9, '11-12...' -> 11."""
    return int(code.split(".", 1)[0].split("-", 1)[0])


def family_levels(family: SkillFamily, secure_through: int, at_grade_level: int,
                  course_grade: int) -> dict[str, int]:
    """Level per code for grades above secure_through up to the course's band; codes at or below are met."""
    codes = [c for c in family.codes if secure_through < grade_key(c) <= course_grade]
    grades = sorted({grade_key(c) for c in codes})
    n = len(grades)
    return {c: max(1, int(at_grade_level * (grades.index(grade_key(c)) + 1) / n + 0.5)) for c in codes}


def combine(*level_maps: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in level_maps:
        for code, level in m.items():
            out[code] = max(out.get(code, 0), level)
    return out
