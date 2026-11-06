-- PROPOSED STUBS — owned by the backend / Data Engineer, not the agentic service.
-- Minimal columns (from docs/sass_agentic_class_diagram.puml) so the agent tables can use
-- foreign keys. The owners replace or extend these through their own migrations.
-- Populated from the LMS (for the MVP: the same synthetic dataset that seeds the mock LMS).

CREATE TABLE teacher (
  id          text PRIMARY KEY,
  lms_user_id text UNIQUE,
  name        text NOT NULL
);

CREATE TABLE classroom (
  id            text PRIMARY KEY,
  lms_course_id text NOT NULL UNIQUE,
  teacher_id    text NOT NULL REFERENCES teacher (id),
  subject       text NOT NULL CHECK (subject IN ('ela', 'math')),
  -- Standards in scope = this grade and every lower grade. 0 = kindergarten.
  grade_level   smallint NOT NULL CHECK (grade_level BETWEEN 0 AND 12),
  period        text,
  name          text NOT NULL
);

CREATE TABLE student (
  id          text PRIMARY KEY,
  lms_user_id text NOT NULL UNIQUE
);

CREATE TABLE enrollment (
  classroom_id text NOT NULL REFERENCES classroom (id),
  student_id   text NOT NULL REFERENCES student (id),
  start_date   date NOT NULL,
  end_date     date,
  PRIMARY KEY (classroom_id, student_id),
  CHECK (end_date IS NULL OR end_date >= start_date)
);

CREATE TABLE assignment (
  id                text PRIMARY KEY,
  lms_assignment_id text NOT NULL UNIQUE,
  classroom_id      text NOT NULL REFERENCES classroom (id),
  title             text NOT NULL,
  due_at            timestamptz,
  submission_type   text NOT NULL CHECK (submission_type IN ('essay', 'quiz'))
);
