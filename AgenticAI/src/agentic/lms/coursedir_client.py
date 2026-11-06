"""Adapter: a v2 course folder (Adaptee) behind the LMSClient Target.

Submission files store the file assignment id; this Adapter serves `<file id>@<version>`.
Ids are resolved only against the course's own assignment list, never used to build paths."""
import html
from contextlib import contextmanager
from pathlib import Path

from agentic.coursedata import (AssignmentFile, CourseDir, SubmissionRow, due_at, file_assignment_id,
                                lms_assignment_id)
from agentic.lms.base import Assignment, Course, Submission


@contextmanager
def _course_data():
    """Missing course data surfaces as LookupError; FileNotFoundError text is a filesystem path."""
    try:
        yield
    except FileNotFoundError:
        raise LookupError("unknown course data") from None


class CourseDirLMSClient:
    def __init__(self, data_dir: Path, version: str):
        self._version = version
        self._dir = CourseDir(Path(data_dir), version)

    def _check_course(self, course_id: str) -> None:
        if course_id != f"classroom_{self._version}":
            raise LookupError(f"unknown course {course_id!r}")

    def _file_assignment(self, assignment_id: str) -> AssignmentFile:
        try:
            file_id, version = file_assignment_id(assignment_id)
            if version != self._version:
                raise LookupError
            with _course_data():
                return next(a for a in self._dir.assignments() if a.id == file_id)
        except (ValueError, StopIteration):
            raise LookupError("unknown assignment") from None
        except LookupError as err:
            raise LookupError("unknown assignment" if not str(err) else str(err)) from None

    def get_course(self, course_id: str) -> Course:
        self._check_course(course_id)
        with _course_data():
            course = self._dir.course()
        return Course(id=course_id, name=course.name, subject=course.subject, grade_level=course.grade)

    def get_assignment(self, course_id: str, assignment_id: str) -> Assignment:
        self._check_course(course_id)
        a = self._file_assignment(assignment_id)
        with _course_data():
            start = self._dir.course().start_date
            strand = next((m.strand for m in self._dir.curriculum().modules if m.id == a.module), None)
        return Assignment(
            id=lms_assignment_id(a.id, self._version), name=a.title, description=a.prompt, notes=a.notes,
            submission_type=a.type, due_at=due_at(start, a.due_offset),
            items=[i.model_dump() for i in a.items], reading_strand=strand,
        )

    def get_submission(self, course_id: str, assignment_id: str, user_id: str) -> Submission:
        self._check_course(course_id)
        a = self._file_assignment(assignment_id)
        with _course_data():
            matches = [r for r in self._dir.submissions(a.id) if r.student_hash == user_id]
        if not matches:
            raise LookupError("no submission found")
        return self._submission(max(matches, key=lambda r: r.attempt))

    def list_submissions(self, course_id: str, assignment_id: str | None = None) -> list[Submission]:
        self._check_course(course_id)
        if assignment_id is None:
            with _course_data():
                file_ids = [a.id for a in self._dir.assignments()]
        else:
            file_ids = [self._file_assignment(assignment_id).id]
        latest: dict[tuple[str, str], SubmissionRow] = {}
        for file_id in file_ids:
            with _course_data():
                rows = self._dir.submissions(file_id)
            for r in rows:
                key = (file_id, r.student_hash)
                if key not in latest or r.attempt > latest[key].attempt:
                    latest[key] = r
        return [self._submission(r) for _, r in sorted(latest.items())]

    def _submission(self, r: SubmissionRow) -> Submission:
        answers = [a.model_dump(exclude_none=True) for a in r.answers or []]
        if r.paragraphs is not None:
            body = "".join(f"<p>{html.escape(p)}</p>" for p in r.paragraphs)
        else:  # short_answer rows carry text; multiple_choice rows carry only choices, so no body
            body = "".join(f"<p>{html.escape(a['item_id'])}: {html.escape(a['text'])}</p>"
                           for a in answers if "text" in a)
        return Submission(
            id=r.submission_id, user_id=r.student_hash,
            assignment_id=lms_assignment_id(r.assignment_id, self._version),
            attempt=r.attempt, body=body, submitted_at=r.submitted_at, answers=answers,
        )
