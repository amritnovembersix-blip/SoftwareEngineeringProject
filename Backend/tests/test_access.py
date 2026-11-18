"""Login protects every data endpoint, and a teacher sees only their own classes and students.

These tests send real tokens (no test-admin shortcut). They add a second teacher with one class
and one student, plus three accounts, and remove them all afterwards."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import agentic_client, auth, db
from app.config import settings
from app.main import app

pytestmark = pytest.mark.real_auth
settings.jwt_secret = settings.jwt_secret or "test-secret-" + "x" * 40
client = TestClient(app)

TAG = uuid.uuid4().hex[:8]
OTHER_TEACHER = f"test-teacher-{TAG}"
OTHER_CLASS = f"classroom_test-{TAG}"
OTHER_STUDENT = f"test-student-{TAG}"


def _ready() -> bool:
    try:
        return bool(db.query("SELECT to_regclass('app_user') IS NOT NULL AS ok")[0]["ok"]
                    and db.query("SELECT 1 FROM classroom WHERE teacher_id = 'demo-teacher' LIMIT 1"))
    except db.DatabaseUnavailable:
        return False


if not _ready():
    pytest.skip("needs Postgres with app_user and the demo data (fill-demo)", allow_module_level=True)


@pytest.fixture(scope="module")
def world():
    """Demo teacher (owns the demo classes), another teacher with one class and one student,
    and an admin. Returns their tokens and one demo student."""
    db.execute("INSERT INTO teacher (id, name) VALUES (%s, 'Other Teacher')", (OTHER_TEACHER,))
    db.execute("""INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name)
                  VALUES (%s, %s, %s, 'ela', 9, 'Other class')""", (OTHER_CLASS, OTHER_CLASS, OTHER_TEACHER))
    db.execute("INSERT INTO student (id, lms_user_id) VALUES (%s, %s)", (OTHER_STUDENT, OTHER_STUDENT))
    db.execute("INSERT INTO enrollment (classroom_id, student_id, start_date) VALUES (%s, %s, CURRENT_DATE)",
               (OTHER_CLASS, OTHER_STUDENT))
    tokens = {}
    for name, role, teacher in [("demo", "teacher", "demo-teacher"),
                                ("other", "teacher", OTHER_TEACHER),
                                ("admin", "admin", None)]:
        uid = db.execute("""INSERT INTO app_user (email, password_hash, role, teacher_id, display_name)
                            VALUES (%s, 'unused', %s, %s, %s) RETURNING id""",
                         (f"{name}-{TAG}@sass.test", role, teacher, name))[0]["id"]
        tokens[name] = {"Authorization": f"Bearer {auth.create_token(uid, role)}"}
    demo_student = db.query("""SELECT e.student_id FROM enrollment e JOIN classroom c ON c.id = e.classroom_id
                               WHERE c.teacher_id = 'demo-teacher' LIMIT 1""")[0]["student_id"]
    demo_class = db.query("SELECT id FROM classroom WHERE teacher_id = 'demo-teacher' ORDER BY id LIMIT 1")[0]["id"]
    yield {"tokens": tokens, "demo_student": demo_student, "demo_class": demo_class}
    db.execute("DELETE FROM app_user WHERE email LIKE %s", (f"%-{TAG}@sass.test",))
    db.execute("DELETE FROM enrollment WHERE classroom_id = %s", (OTHER_CLASS,))
    db.execute("DELETE FROM student WHERE id = %s", (OTHER_STUDENT,))
    db.execute("DELETE FROM classroom WHERE id = %s", (OTHER_CLASS,))
    db.execute("DELETE FROM teacher WHERE id = %s", (OTHER_TEACHER,))


def _protected(w):
    c, s = w["demo_class"], w["demo_student"]
    return [("GET", "/classrooms"),
            ("GET", f"/classrooms/{c}/students"),
            ("POST", f"/classrooms/{c}/reviews"),
            ("GET", "/reviews/some-run"),
            ("GET", f"/students/{s}/findings"),
            ("GET", f"/students/{s}/concepts"),
            ("GET", f"/students/{s}/concept-history")]


def test_every_data_endpoint_needs_a_login(world):
    for method, path in _protected(world):
        r = client.request(method, path)
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.headers["www-authenticate"] == "Bearer"


def test_a_bad_token_is_rejected(world):
    for method, path in _protected(world):
        r = client.request(method, path, headers={"Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401, (method, path)


def test_health_and_login_stay_open(world):
    assert client.get("/health").status_code in (200, 503)   # never 401
    assert client.post("/auth/login", json={"email": "x@y.test", "password": "x"}).status_code == 401


def test_a_teacher_lists_only_their_own_classrooms(world):
    t = world["tokens"]
    demo = {c["id"] for c in client.get("/classrooms", headers=t["demo"]).json()}
    other = {c["id"] for c in client.get("/classrooms", headers=t["other"]).json()}
    admin = {c["id"] for c in client.get("/classrooms", headers=t["admin"]).json()}
    assert other == {OTHER_CLASS}
    assert OTHER_CLASS not in demo and world["demo_class"] in demo
    assert admin >= demo | other


def test_another_teachers_class_and_students_are_403(world, monkeypatch):
    monkeypatch.setattr(agentic_client, "start_sweep", lambda course: pytest.fail("must not start a review"))
    other = world["tokens"]["other"]
    c, s = world["demo_class"], world["demo_student"]
    for method, path in [("GET", f"/classrooms/{c}/students"),
                         ("POST", f"/classrooms/{c}/reviews"),
                         ("GET", f"/students/{s}/findings"),
                         ("GET", f"/students/{s}/concepts"),
                         ("GET", f"/students/{s}/concept-history")]:
        r = client.request(method, path, headers=other)
        assert r.status_code == 403, (method, path, r.status_code)


def test_own_class_and_students_work(world):
    t = world["tokens"]
    rows = client.get(f"/classrooms/{OTHER_CLASS}/students", headers=t["other"]).json()
    assert [r["student_id"] for r in rows] == [OTHER_STUDENT]
    assert client.get(f"/students/{OTHER_STUDENT}/concepts", headers=t["other"]).status_code == 200
    s = world["demo_student"]
    assert client.get(f"/students/{s}/concepts", headers=t["demo"]).status_code == 200
    assert client.get(f"/classrooms/{world['demo_class']}/students", headers=t["demo"]).status_code == 200


def test_admin_sees_every_class_and_student(world):
    admin = world["tokens"]["admin"]
    assert client.get(f"/classrooms/{OTHER_CLASS}/students", headers=admin).status_code == 200
    assert client.get(f"/students/{world['demo_student']}/findings", headers=admin).status_code == 200


def test_missing_is_404_even_for_another_teacher(world):
    other = world["tokens"]["other"]
    assert client.get("/classrooms/nope/students", headers=other).status_code == 404
    assert client.get("/students/nope/concepts", headers=other).status_code == 404


def test_a_disabled_account_loses_access_at_once(world):
    uid = db.execute("""INSERT INTO app_user (email, password_hash, role, teacher_id, display_name)
                        VALUES (%s, 'unused', 'teacher', 'demo-teacher', 'x') RETURNING id""",
                     (f"disabled-{TAG}@sass.test",))[0]["id"]
    h = {"Authorization": f"Bearer {auth.create_token(uid, 'teacher')}"}
    assert client.get("/classrooms", headers=h).status_code == 200
    db.execute("UPDATE app_user SET is_active = false WHERE id = %s", (uid,))
    assert client.get("/classrooms", headers=h).status_code == 401
