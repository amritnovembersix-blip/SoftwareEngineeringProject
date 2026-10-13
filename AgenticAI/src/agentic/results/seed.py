"""File-LMS utility: seed the LMS-owned stub tables (teacher, classroom, students, assignment) from a
classroom's files, so reviews run against the JSONL LMS can persist. In production these rows come from
the backend's LMS sync."""
from pathlib import Path

from agentic import dataset


def seed_demo(conn, data_dir: Path, version: str) -> int:
    """Upsert the demo teacher, classroom, students and assignment. Returns the student count."""
    cid = dataset.course_id(version)
    aid = dataset.assignment_id(version)
    grade = dataset.classroom_grade(data_dir, version)
    hashes = sorted({e.student_hash for e in dataset.load_essays(data_dir, version)})
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
        conn.execute(
            "INSERT INTO assignment (id, lms_assignment_id, classroom_id, title, submission_type) "
            "VALUES (%s, %s, %s, 'Essay 1', 'essay') ON CONFLICT (id) DO UPDATE SET "
            "classroom_id = EXCLUDED.classroom_id", (aid, aid, cid))
    return len(hashes)
