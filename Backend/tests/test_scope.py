"""own_classrooms: the SQL condition that limits a student's data to the teacher's own classes.
No database needed."""
from app.access import own_classrooms
from app.auth import CurrentUser


def _user(role: str, teacher_id: str | None) -> CurrentUser:
    return CurrentUser(id=1, email="x@sass.test", role=role, teacher_id=teacher_id, display_name="x")


def test_admin_sees_everything():
    assert own_classrooms(_user("admin", None), "a.classroom_id") == ("true", ())


def test_teacher_is_limited_to_their_classrooms():
    sql, params = own_classrooms(_user("teacher", "t-1"), "c.classroom_id")
    assert sql == "c.classroom_id IN (SELECT id FROM classroom WHERE teacher_id = %s)"
    assert params == ("t-1",)
