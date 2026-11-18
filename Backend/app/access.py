"""Who may see what. Every data endpoint needs a logged-in user (app.auth.current_user):

- An **admin** sees every classroom and student.
- A **teacher** sees only the classrooms they teach (classroom.teacher_id) and the students
  enrolled in them. Anything else is 403.

Missing things are still 404 (checked first), so a teacher learns nothing new from a 403 about a
classroom ID they guessed: it exists, and that's all.
"""
from fastapi import HTTPException, status

from app import db
from app.auth import CurrentUser


def _forbidden(what: str) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, f"this {what} is not in your classes")


def classroom_filter(user: CurrentUser) -> tuple[str, tuple]:
    """A SQL condition on classroom alias `c` limiting rows to what the user may see."""
    if user.is_admin:
        return "true", ()
    return "c.teacher_id = %s", (user.teacher_id,)


def require_classroom(user: CurrentUser, classroom_id: str) -> None:
    """404 if the classroom doesn't exist, 403 if it isn't this teacher's."""
    rows = db.query("SELECT teacher_id FROM classroom WHERE id = %s", (classroom_id,))
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "classroom not found")
    if not user.is_admin and rows[0]["teacher_id"] != user.teacher_id:
        raise _forbidden("classroom")


def require_student(user: CurrentUser, student_id: str) -> None:
    """404 if the student doesn't exist, 403 if they were never enrolled in this teacher's classes.

    Past enrollments count too, so a teacher can still see work from a student who has left."""
    if not db.query("SELECT 1 FROM student WHERE id = %s", (student_id,)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "student not found")
    if user.is_admin:
        return
    if not db.query("""
            SELECT 1 FROM enrollment e JOIN classroom c ON c.id = e.classroom_id
            WHERE e.student_id = %s AND c.teacher_id = %s LIMIT 1""", (student_id, user.teacher_id)):
        raise _forbidden("student")
