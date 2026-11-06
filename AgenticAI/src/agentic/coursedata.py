"""Course-folder formats (v2 synthetic data): curriculum, assignment files, rosters, submissions.

Read by the CourseDirLMSClient Adapter. Never reads `answer_key/` or `skill_families.json` (lab only)."""
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from agentic.dataset import read_rows

_CENTRAL = ZoneInfo("America/Chicago")
_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
T = TypeVar("T", bound=BaseModel)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Module(_Strict):
    id: str
    title: str
    strand: Literal["RL", "RI"]
    weeks: tuple[int, int]


class Curriculum(_Strict):
    id: str
    grade: int
    modules: list[Module]
    assignments: list[str]  # file assignment ids, in order


class Choice(_Strict):
    key: str
    text: str
    correct: bool = False
    misconception: str | None = None
    standard: str | None = None

    @model_validator(mode="after")
    def _correct_or_diagnostic(self):
        if not self.correct and not (self.misconception and self.standard):
            raise ValueError("a choice is correct, or has both misconception and standard")
        return self


class Item(_Strict):
    id: str
    standard: str
    prompt: str | None = None  # short_answer
    stem: str | None = None  # multiple_choice
    choices: list[Choice] = []


class DueOffset(_Strict):
    week: int  # 1 = the course's start week
    day: Literal["mon", "tue", "wed", "thu", "fri"]


class AssignmentFile(_Strict):
    id: str
    module: str
    week: int
    kind: Literal["regular", "intervention"]
    type: Literal["essay", "paragraph", "short_answer", "multiple_choice"]
    title: str
    prompt: str
    text_id: str
    extra_text_ids: list[str] = []  # further texts the prompt compares with `text_id`, in order
    due_offset: DueOffset
    assigned_to: list[str] | None = None  # None = everyone
    targets: list[str] = []
    notes: dict[str, str] = {}
    items: list[Item] = []

    @model_validator(mode="after")
    def _items_match_type(self):
        if self.type in ("essay", "paragraph"):
            if self.items:
                raise ValueError(f"{self.type} assignments have no items")
        elif not self.items:
            raise ValueError(f"{self.type} assignments need items")
        if self.type == "multiple_choice":
            for item in self.items:
                if sum(c.correct for c in item.choices) != 1:
                    raise ValueError("each multiple_choice item needs exactly one correct choice")
        return self


class CourseFile(_Strict):
    id: str  # classroom_<version>
    name: str
    grade: int
    subject: Literal["ela"]
    teacher_id: str
    period: int
    start_date: date
    arm: Literal["control", "intervention"]
    curriculum: str
    twin: str | None = None  # the twin course's version


class RosterRow(_Strict):
    student_hash: str


class InterventionRow(_Strict):
    id: str
    week: int
    kind: Literal["class_lesson", "small_group", "individual"]
    students: list[str] | Literal["all"]
    families: list[str]
    standards: list[str]
    assignment_id: str | None = None
    description: str


class ItemAnswer(_Strict):
    item_id: str
    text: str | None = None
    choice: str | None = None


class SubmissionRow(_Strict):
    student_hash: str
    submission_id: str
    attempt: int
    assignment_id: str  # the file assignment id
    course_id: str  # classroom_<version>
    submitted_at: datetime
    paragraphs: list[str] | None = None
    answers: list[ItemAnswer] | None = None

    @model_validator(mode="after")
    def _one_body(self):
        if (self.paragraphs is None) == (self.answers is None):
            raise ValueError("exactly one of paragraphs / answers")
        return self


def course_path(data_dir: Path, version: str) -> Path:
    return Path(data_dir) / "courses" / version


def curriculum_path(data_dir: Path, curriculum_id: str) -> Path:
    return Path(data_dir) / "curricula" / curriculum_id


def due_at(start: date, offset: DueOffset) -> datetime:
    """23:59 America/Chicago on the offset's day, as UTC."""
    day = start + timedelta(weeks=offset.week - 1, days=_DAYS[offset.day] - start.weekday())
    return datetime.combine(day, time(23, 59), tzinfo=_CENTRAL).astimezone(timezone.utc)


def lms_assignment_id(file_id: str, version: str) -> str:
    return f"{file_id}@{version}"


def file_assignment_id(lms_id: str) -> tuple[str, str]:
    file_id, sep, version = lms_id.partition("@")
    if not sep or not file_id or not version:
        raise ValueError("assignment id must look like <file id>@<version>")
    return file_id, version


def read_json(path: Path, model: type[T]) -> T:
    """Read one JSON file into a model; errors name the file and fields, never content."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    problem = None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raw = None
        problem = f"malformed JSON in {path.name}"
    if problem is None:
        try:
            return model.model_validate(raw)
        except ValidationError as err:
            problem = f"invalid file {path.name}: fields " + str(
                sorted({".".join(str(x) for x in e["loc"]) or "(model)" for e in err.errors()}))
    raise ValueError(problem)  # outside the except blocks so no content is chained


class CourseDir:
    """One course folder plus its shared curriculum, read on demand."""

    def __init__(self, data_dir: Path, version: str):
        self.data_dir, self.version = Path(data_dir), version
        self.path = course_path(data_dir, version)

    def course(self) -> CourseFile:
        return read_json(self.path / "course.json", CourseFile)

    def _curriculum_dir(self) -> Path:
        return curriculum_path(self.data_dir, self.course().curriculum)

    def curriculum(self) -> Curriculum:
        return read_json(self._curriculum_dir() / "curriculum.json", Curriculum)

    def assignments(self) -> list[AssignmentFile]:
        """The curriculum's assignments in order, then the course's own by id."""
        shared = self._curriculum_dir() / "assignments"
        out = [read_json(shared / f"{a}.json", AssignmentFile) for a in self.curriculum().assignments]
        own = sorted((self.path / "assignments").glob("*.json"))
        return out + [read_json(p, AssignmentFile) for p in own]

    def assignment(self, file_id: str) -> AssignmentFile:
        for a in self.assignments():
            if a.id == file_id:
                return a
        raise LookupError(f"unknown assignment {file_id!r} in {self.version}")

    def roster(self) -> list[RosterRow]:
        return read_rows(self.path / "roster.jsonl", RosterRow)

    def resolve_student(self, prefix: str) -> str:
        """The one roster student whose hash starts with `prefix` (LookupError otherwise)."""
        try:
            roster = self.roster()
        except FileNotFoundError:
            raise LookupError(f"unknown course {self.version!r}") from None
        matches = [r.student_hash for r in roster if r.student_hash.startswith(prefix)]
        if not matches:
            raise LookupError(f"no student matches prefix {prefix!r}")
        if len(matches) > 1:
            raise LookupError(f"student prefix {prefix!r} is ambiguous ({len(matches)} matches)")
        return matches[0]

    def interventions(self) -> list[InterventionRow]:
        path = self.path / "interventions.json"
        if not path.exists():
            raise FileNotFoundError(str(path))
        problem = None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return [InterventionRow.model_validate(r) for r in raw]
        except (ValueError, TypeError):  # ValidationError is a ValueError
            problem = f"invalid file {path.name}"
        raise ValueError(problem)

    def submissions(self, file_id: str) -> list[SubmissionRow]:
        path = self.path / "submissions" / f"{file_id}.jsonl"
        return read_rows(path, SubmissionRow) if path.exists() else []

    def text(self, text_id: str) -> str:
        return (self._curriculum_dir() / "texts" / f"{text_id}.md").read_text(encoding="utf-8")
