-- Login accounts for the SASS web app. The Backend owns this table (auth: /auth/login, /auth/me); it lives here so
-- Flyway creates it with the rest of the shared schema (team PR #17 first added it as Backend/migrations/001_app_user.sql,
-- run by hand). IF NOT EXISTS: a database that already ran that script keeps its accounts.
-- The `teacher` table comes from the LMS sync and has no password, so logins live here. A teacher account links to
-- its `teacher` row and sees only that teacher's classrooms; an admin account has no teacher row and sees every one.
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

COMMENT ON TABLE app_user IS 'Backend-owned login accounts. Passwords are bcrypt hashes only. No student data.';

-- The Backend records the deciding user's email (an admin has no teacher id); V8 said "teacher's id".
COMMENT ON COLUMN peer_pair_suggestion.decided_by IS 'Email of the user (teacher or admin) who approved or rejected the pair.';
