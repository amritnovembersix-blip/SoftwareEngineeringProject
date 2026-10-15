"""Target for the LMS client."""
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel


class Course(BaseModel):
    id: str
    name: str
    subject: Literal["ela", "math"]
    grade_level: int


class Submission(BaseModel):
    id: str
    user_id: str
    assignment_id: str
    attempt: int
    body: str
    submitted_at: datetime
    workflow_state: str = "submitted"
    late: bool = False
    missing: bool = False
    excused: bool = False


class Assignment(BaseModel):
    """Canvas field names: `name` is the title, `description` the teacher's instructions."""
    id: str
    name: str
    description: str | None = None
    # Lab extension: per-standard focus notes (in Canvas, rubric criteria aligned to outcomes).
    notes: dict[str, str] = {}


@runtime_checkable
class LMSClient(Protocol):
    def get_course(self, course_id: str) -> Course: ...

    def get_assignment(self, course_id: str, assignment_id: str) -> Assignment: ...

    def get_submission(self, course_id: str, assignment_id: str, user_id: str) -> Submission: ...

    def list_submissions(self, course_id: str, assignment_id: str | None = None) -> list[Submission]:
        """The latest attempt of each student's submission in the course (or one assignment)."""
        ...
