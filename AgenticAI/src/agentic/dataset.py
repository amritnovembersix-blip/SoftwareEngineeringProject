"""File-LMS formats (essays, classroom assignment info, IDs) used by the JSONL LMS Adapter.

Labels (the evaluation ground truth) are a lab concern: see agentic_lab.labels."""
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator


class EssayRow(BaseModel):
    student_hash: str
    submission_id: str
    attempt: int
    assignment_id: str
    course_id: str
    submitted_at: datetime
    paragraphs: list[str]


AssignmentType = Literal["argument", "rl", "ri"]
WRITING = "W"
_DEFAULT_STRANDS = {"argument": [WRITING], "rl": [WRITING, "RL"], "ri": [WRITING, "RI"]}


class AssignmentInfo(BaseModel):
    """What a classroom's assignment is (not the answers): safe for the reviewer to read."""
    id: str = "argument"
    type: AssignmentType = "argument"
    text: str | None = None
    title: str | None = None
    prompt: str | None = None  # the teacher's wording (Canvas `description`)
    targets: list[str] = []  # targeted standard codes; empty = strands decide the scope
    notes: dict[str, str] = {}  # per-target focus, e.g. {"9-10.RL.1.D": "one theme only"}
    strands: list[str] = [WRITING]

    @field_validator("targets")
    @classmethod
    def _clean_targets(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(c.strip() for c in v if c.strip()))

    @field_validator("notes")
    @classmethod
    def _clean_notes(cls, v: dict[str, str]) -> dict[str, str]:
        return {k.strip(): n.strip() for k, n in v.items() if k.strip() and n.strip()}


def assignment_path(data_dir: Path, version: str) -> Path:
    return Path(data_dir) / f"classroom_{version}.assignment.json"


def normalize_strands(strands) -> list[str]:
    """Upper-case, de-duplicated, writing (W) always first."""
    rest = [s.strip().upper() for s in strands if s.strip() and s.strip().upper() != WRITING]
    return [WRITING] + list(dict.fromkeys(rest))


def load_assignment(data_dir: Path, version: str) -> AssignmentInfo:
    """A missing file means a generic argumentative essay reviewed against writing (W) only."""
    path = assignment_path(data_dir, version)
    return read_assignment(path) if path.exists() else AssignmentInfo()


def read_assignment(path: Path) -> AssignmentInfo:
    """Read one assignment file (a classroom's, or a template in agentic_lab/data/synthetic/assignments/)."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        info = AssignmentInfo.model_validate({k: v for k, v in raw.items() if k != "strands"})
    except (ValueError, ValidationError, AttributeError):
        info = None  # raise below, outside the except, so no content is chained
    if info is None:
        raise ValueError(f"invalid assignment file {path.name}: expected type argument|rl|ri")
    strands = raw.get("strands") or _DEFAULT_STRANDS[info.type]
    return info.model_copy(update={"strands": normalize_strands(strands)})


def labels_path(data_dir: Path, version: str) -> Path:
    return Path(data_dir) / f"classroom_{version}.jsonl"


def essays_path(data_dir: Path, version: str) -> Path:
    return Path(data_dir) / f"essays_classroom_{version}.jsonl"


def read_rows(path: Path, model: type[BaseModel] | None = None) -> list:
    """Read JSONL rows; errors never include row content (essay text)."""
    if not path.exists():
        raise FileNotFoundError(str(path))
    rows = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            problem = None
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                problem = f"malformed JSON on line {i} in {path.name}"
            if problem:  # raised outside the except block so no context is chained
                raise ValueError(problem)
            if model is None:
                rows.append(row)
                continue
            try:
                rows.append(model.model_validate(row))
            except ValidationError as err:
                problem = f"invalid row {i} in {path.name}: fields " + str(
                    [".".join(str(x) for x in e["loc"]) for e in err.errors()])
            if problem:
                raise ValueError(problem)
    return rows


def load_essays(data_dir: Path, version: str) -> list[EssayRow]:
    return read_rows(essays_path(data_dir, version), EssayRow)


def _grade_only(data_dir: Path, version: str) -> int:
    """Read only `grade` from the labels file (never expose underdeveloped labels)."""
    grades = {r["grade"] for r in read_rows(labels_path(data_dir, version))}
    if len(grades) != 1:
        raise ValueError(f"classroom_{version} rows disagree on grade: {sorted(grades)}")
    return int(next(iter(grades)))


def classroom_grade(data_dir: Path, version: str) -> int:
    return _grade_only(data_dir, version)


def course_id(version: str) -> str:
    return f"classroom_{version}"


def assignment_id(version: str) -> str:
    return f"asg-classroom_{version}-essay1"


def resolve_student(essays: list[EssayRow], prefix: str) -> EssayRow:
    matches = [e for e in essays if e.student_hash.startswith(prefix)]
    hashes = {e.student_hash for e in matches}
    if not hashes:
        raise LookupError(f"no student matches {prefix!r}")
    if len(hashes) > 1:
        raise LookupError(f"ambiguous: {len(hashes)} students match {prefix!r}")
    return matches[0]
