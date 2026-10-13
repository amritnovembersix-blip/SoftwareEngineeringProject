"""Adapter: local synthetic JSONL files (Adaptee) behind the LMSClient Target."""
import html
from pathlib import Path

from agentic import dataset
from agentic.lms.base import Assignment, Course, Submission


class JsonlLMSClient:
    def __init__(self, data_dir: Path, version: str):
        self._data_dir = Path(data_dir)
        self._version = version

    def get_course(self, course_id: str) -> Course:
        if course_id != dataset.course_id(self._version):
            raise LookupError(f"unknown course {course_id!r}")
        return Course(
            id=course_id,
            name=f"Classroom {self._version}",
            subject="ela",
            grade_level=dataset.classroom_grade(self._data_dir, self._version),
        )

    def get_assignment(self, course_id: str, assignment_id: str) -> Assignment:
        if course_id != dataset.course_id(self._version):
            raise LookupError(f"unknown course {course_id!r}")
        info = dataset.load_assignment(self._data_dir, self._version)
        return Assignment(id=assignment_id, name=info.title or info.id, description=info.prompt,
                          notes=info.notes)

    def get_submission(self, course_id: str, assignment_id: str, user_id: str) -> Submission:
        matches = [
            e for e in dataset.load_essays(self._data_dir, self._version)
            if e.course_id == course_id and e.assignment_id == assignment_id and e.student_hash == user_id
        ]
        if not matches:
            raise LookupError("no submission found")
        e = max(matches, key=lambda r: r.attempt)
        body = "".join(f"<p>{html.escape(p)}</p>" for p in e.paragraphs)
        return Submission(
            id=e.submission_id, user_id=e.student_hash, assignment_id=e.assignment_id,
            attempt=e.attempt, body=body, submitted_at=e.submitted_at,
        )
