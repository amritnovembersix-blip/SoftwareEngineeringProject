"""Answer-key formats for v2 course folders (lab only; production never opens `answer_key/`).

See the synthetic-data v2 design spec, section 2.4. Levels: 1 Approaching, 2 Developing, 3 Beginning; absent
means meets.
"""
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentic.coursedata import course_path


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FamilyEntry(_Strict):
    family: str
    secure_through: int
    at_grade_level: int


class StandardLevel(_Strict):
    standard: str
    level: int


class StudentKey(_Strict):
    student_hash: str = ""  # empty until the student is placed in an arm
    pair_id: str
    profile: str
    families: list[FamilyEntry]
    quirks: list[StandardLevel]
    baseline: dict[str, int]
    start_week: int
    non_responder: bool = False
    non_responder_iv: str | None = None  # the intervention the non-responder does not respond to


class TrajectoryRow(_Strict):
    student_hash: str
    week: int
    standard: str
    level: int


class AnyOf(_Strict):
    """One planted gap that any of `codes` describes (phase 3): a family left with no code once Revise and Edit is
    dropped from a paragraph or short answer gets its highest grade 3-5 codes as one slot."""
    family: str
    codes: list[str]
    level: int


class ExpectedRow(_Strict):
    student_hash: str
    submission_id: str
    weaknesses: list[StandardLevel]
    any_of: list[AnyOf] = []


class FidelityRow(_Strict):
    submission_id: str
    missing: list[str]
    extra: list[str]


def answer_key_path(data_dir: Path, version: str) -> Path:
    return course_path(data_dir, version) / "answer_key"
