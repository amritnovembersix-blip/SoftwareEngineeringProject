"""When each assignment is due and when its grading event fires."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agentic.coursedata import CourseDir, due_at, lms_assignment_id

_CENTRAL = ZoneInfo("America/Chicago")


@dataclass(frozen=True)
class ScheduledAssignment:
    file_id: str
    lms_id: str
    course_id: str
    version: str
    week: int  # the assignment's due week
    due_at: datetime  # UTC
    event_time: datetime  # UTC


def event_time_for(due: datetime) -> datetime:
    """The Monday strictly after the due date's local date, 08:00 America/Chicago, as UTC."""
    local_day = due.astimezone(_CENTRAL).date()
    monday = local_day + timedelta(days=7 - local_day.weekday())
    return datetime.combine(monday, time(8, 0), tzinfo=_CENTRAL).astimezone(timezone.utc)


def schedule(data_dir: Path, version: str) -> list[ScheduledAssignment]:
    """Every assignment of the course, sorted by (due_at, file_id)."""
    course_dir = CourseDir(data_dir, version)
    course = course_dir.course()
    items = []
    for a in course_dir.assignments():
        due = due_at(course.start_date, a.due_offset)
        items.append(ScheduledAssignment(
            file_id=a.id, lms_id=lms_assignment_id(a.id, version), course_id=course.id,
            version=version, week=a.due_offset.week, due_at=due, event_time=event_time_for(due)))
    return sorted(items, key=lambda i: (i.due_at, i.file_id))


def due_in_week(items: list[ScheduledAssignment], week: int) -> list[ScheduledAssignment]:
    return [i for i in items if i.week == week]
