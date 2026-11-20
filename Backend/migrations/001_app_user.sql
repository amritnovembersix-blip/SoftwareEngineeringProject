-- Login accounts for the SASS web app (backend-owned).
--
-- The `teacher` table comes from the LMS sync and has no password, so logins live here.
-- A teacher account links to its `teacher` row, so it sees only that teacher's classrooms.
-- An admin account has no teacher row and sees every classroom.
--
-- Run once, after AgenticAI's migrations:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f Backend/migrations/001_app_user.sql

CREATE TABLE IF NOT EXISTS app_user (
  id            bigserial   PRIMARY KEY,
  email         text        NOT NULL UNIQUE CHECK (email = lower(email)),
  password_hash text        NOT NULL,                       -- bcrypt, never the plain password
  role          text        NOT NULL CHECK (role IN ('teacher', 'admin')),
  teacher_id    text        REFERENCES teacher (id),
  display_name  text        NOT NULL,
  is_active     boolean     NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz,
  -- a teacher account must point at a teacher; an admin account must not
  CHECK ((role = 'teacher') = (teacher_id IS NOT NULL))
);
