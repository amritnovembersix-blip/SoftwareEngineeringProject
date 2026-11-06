"""File-LMS utility: seed the LMS-owned stub tables (teacher, classroom, students, enrollments,
assignment) from a classroom's files, so reviews run against the JSONL LMS can persist and the class's
students are listed before any review. In production these rows come from the backend's LMS sync."""
from pathlib import Path

from agentic import dataset
from agentic.coursedata import CourseDir, due_at, lms_assignment_id


def seed_demo(conn, data_dir: Path, version: str) -> int:
    """Upsert the demo teacher, classroom, students, enrollments and assignment. Returns the student count."""
    cid = dataset.course_id(version)
    aid = dataset.assignment_id(version)
    grade = dataset.classroom_grade(data_dir, version)
    essays = dataset.load_essays(data_dir, version)
    hashes = sorted({e.student_hash for e in essays})
    start = min(e.submitted_at for e in essays).date()  # enrolled no later than their first essay
    with conn.transaction():
        conn.execute("INSERT INTO teacher (id, name) VALUES ('demo-teacher', 'Demo Teacher') "
                     "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name")
        conn.execute(
            "INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name) "
            "VALUES (%s, %s, 'demo-teacher', 'ela', %s, %s) ON CONFLICT (id) DO UPDATE SET "
            "grade_level = EXCLUDED.grade_level, name = EXCLUDED.name",
            (cid, cid, grade, f"Classroom {version}"))
        for h in hashes:
            conn.execute("INSERT INTO student (id, lms_user_id) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                         (h, h))
            conn.execute("INSERT INTO enrollment (classroom_id, student_id, start_date) VALUES (%s, %s, %s) "
                         "ON CONFLICT (classroom_id, student_id) DO NOTHING", (cid, h, start))
        conn.execute(
            "INSERT INTO assignment (id, lms_assignment_id, classroom_id, title, submission_type) "
            "VALUES (%s, %s, %s, 'Essay 1', 'essay') ON CONFLICT (id) DO UPDATE SET "
            "classroom_id = EXCLUDED.classroom_id", (aid, aid, cid))
    return len(hashes)


_SUBMISSION_TYPE = {"essay": "essay", "paragraph": "essay", "short_answer": "quiz", "multiple_choice": "quiz"}


def seed_course(conn, data_dir: Path, version: str) -> dict[str, int]:
    """Upsert a v2 course folder and its twin (course.json `twin`): demo teacher, classrooms, students,
    enrollments and assignments. No submissions. Returns the classroom, distinct student and assignment counts."""
    first = CourseDir(data_dir, version)
    versions = [version] + ([first.course().twin] if first.course().twin else [])
    students: set[str] = set()
    assignments = 0
    with conn.transaction():
        conn.execute("INSERT INTO teacher (id, name) VALUES ('demo-teacher', 'Demo Teacher') "
                     "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name")
        for v in versions:
            cd = CourseDir(data_dir, v)
            course = cd.course()
            conn.execute(
                "INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, period, name) "
                "VALUES (%s, %s, 'demo-teacher', %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
                "grade_level = EXCLUDED.grade_level, period = EXCLUDED.period, name = EXCLUDED.name",
                (course.id, course.id, course.subject, course.grade, str(course.period), course.name))
            for row in cd.roster():
                h = row.student_hash
                students.add(h)
                conn.execute("INSERT INTO student (id, lms_user_id) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                             (h, h))
                conn.execute("INSERT INTO enrollment (classroom_id, student_id, start_date) VALUES (%s, %s, %s) "
                             "ON CONFLICT (classroom_id, student_id) DO NOTHING", (course.id, h, course.start_date))
            for a in cd.assignments():
                lid = lms_assignment_id(a.id, v)
                conn.execute(
                    "INSERT INTO assignment (id, lms_assignment_id, classroom_id, title, due_at, submission_type) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
                    "classroom_id = EXCLUDED.classroom_id, title = EXCLUDED.title, due_at = EXCLUDED.due_at, "
                    "submission_type = EXCLUDED.submission_type",
                    (lid, lid, course.id, a.title, due_at(course.start_date, a.due_offset),
                     _SUBMISSION_TYPE[a.type]))
                assignments += 1
    return {"classrooms": len(versions), "students": len(students), "assignments": assignments}
