"""Lab only: a results reader built from a v2 course's answer key, so the analytics and the prototype run
with no paid reviews. Production never opens `answer_key/`."""
from pathlib import Path

from agentic.analytics.memory import InMemoryResultsReader
from agentic.analytics.models import VerdictRow
from agentic.coursedata import CourseDir, due_at
from agentic.dataset import read_rows

from agentic_lab.answer_key import ExpectedRow, answer_key_path

SUPPORT = {1: "approaching", 2: "developing", 3: "beginning"}
_TYPE = {"essay": "essay", "paragraph": "essay", "short_answer": "quiz", "multiple_choice": "quiz"}


def _expected(data_dir: Path, version: str, file_id: str) -> list[ExpectedRow]:
    path = answer_key_path(data_dir, version) / "expected" / f"{file_id}.jsonl"
    return read_rows(path, ExpectedRow) if path.exists() else []


def evidenced_standards(assignment, own: list[ExpectedRow], twin: list[ExpectedRow]) -> set[str]:
    """What an assignment shows: its targets and item standards, plus every standard planted on it in this
    course or its twin (so a concept one arm has fully recovered still yields `meets` points)."""
    return set(assignment.targets) | {i.standard for i in assignment.items} | {
        w.standard for r in [*own, *twin] for w in r.weaknesses}


def results_from_answer_key(data_dir: Path, version: str) -> InMemoryResultsReader:
    """One course folder's released submissions as verdict rows. The classroom id is the course's `id` and the
    student id the roster hash, as `seed-demo` uses; the submission id is the expected file's.

    A planted standard is a `growth_area` at its planted level. The standards an assignment evidences are the
    assignment's targets and item standards plus every standard planted on it in this course or its twin
    (the twin keeps the evidence set the same in both arms, so a concept one arm has fully recovered still
    yields `meets` points); each evidenced standard that is not planted for a student is `meets`.
    An assignment with no expected file is skipped. `due_at` is the assignment's due date, `submitted_at` the same.
    """
    cd = CourseDir(data_dir, version)
    course = cd.course()
    rows: list[VerdictRow] = []
    for a in cd.assignments():
        own = _expected(data_dir, version, a.id)
        if not own:
            continue
        twin = _expected(data_dir, course.twin, a.id) if course.twin else []
        evidenced = evidenced_standards(a, own, twin)
        due = due_at(course.start_date, a.due_offset)
        for r in own:
            planted = {w.standard: w.level for w in r.weaknesses}
            for std in sorted(evidenced):
                level = planted.get(std, 0)
                rows.append(VerdictRow(
                    student_id=r.student_hash, classroom_id=course.id, submission_id=r.submission_id,
                    due_at=due, submitted_at=due, submission_type=_TYPE[a.type], standard_code=std,
                    verdict="growth_area" if level else "meets", support_level=SUPPORT.get(level),
                    notable_strength=False, confidence=None, finding_condition=None, overruled=False))
    return InMemoryResultsReader(rows, classrooms={course.id})
