-- Agent-written review tables (proposed: owned by the agentic service).
-- PRIVACY: no table stores student submission text.
-- IMMUTABILITY: the application never UPDATEs or DELETEs observation or finding rows.

-- Word count that treats any whitespace (spaces, tabs, newlines) as a separator and ignores
-- leading/trailing whitespace. Blank text counts as 0 words.
CREATE FUNCTION word_count(t text)
RETURNS integer
LANGUAGE sql IMMUTABLE
AS $$
  SELECT CASE WHEN t ~ '^\s*$' THEN 0
              ELSE cardinality(regexp_split_to_array(regexp_replace(t, '^\s+|\s+$', '', 'g'), '\s+'))
         END
$$;

CREATE TABLE review_run (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_type         text NOT NULL CHECK (run_type IN ('submission_review', 'trend_analysis')),
  trigger_type     text NOT NULL CHECK (trigger_type IN ('on_grade', 'on_due_date', 'scheduled', 'manual', 'after_review')),
  status           text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
  scope            jsonb NOT NULL DEFAULT '{}'::jsonb,   -- the request scope, e.g. {"course_id": "..."}
  submission_count integer NOT NULL DEFAULT 0 CHECK (submission_count >= 0),
  completed_count  integer NOT NULL DEFAULT 0 CHECK (completed_count >= 0),
  skipped_count    integer NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
  skips            jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{"submission_id": "<LMS id>", "attempt": 2, "reason": "excused"}]
  -- Operator-facing summary only. PRIVACY: never include submission text or chunk content
  -- (e.g. from an exception message that echoes its input). Capped to discourage dumps.
  error            text CHECK (char_length(error) <= 500),
  created_at       timestamptz NOT NULL DEFAULT now(),
  started_at       timestamptz,
  finished_at      timestamptz
);

-- One row per attempt the agent has claimed for review. The unique key is the idempotency key
-- for every trigger.
--
-- Review protocol (so failed reviews can be retried without duplicate or orphaned results):
--   1. Claim: INSERT the row with review_status = 'in_progress' (a unique violation means another
--      trigger already claimed it -> return that row's review_run_id as a duplicate).
--   2. Persist: write ALL observations/findings AND set review_status = 'succeeded',
--      reviewed_at = now() in ONE transaction. A failed run therefore leaves no partial results.
--   3. Fail: set review_status = 'failed'. A later sweep retries by UPDATEing review_run_id and
--      review_status back to 'in_progress' (the failed run keeps its own review_run row and error).
--   An 'in_progress' row whose run is no longer running (e.g. a crashed worker) is treated as
--   failed by the next sweep.
CREATE TABLE submission (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lms_submission_id text NOT NULL,
  attempt           integer NOT NULL CHECK (attempt >= 1),
  assignment_id     text NOT NULL REFERENCES assignment (id),
  student_id        text NOT NULL REFERENCES student (id),
  submitted_at      timestamptz NOT NULL,
  cached_due_date   timestamptz,                  -- the student's effective due date (extensions)
  late              boolean NOT NULL DEFAULT false,
  review_run_id     uuid NOT NULL REFERENCES review_run (id),
  review_status     text NOT NULL DEFAULT 'in_progress'
                      CHECK (review_status IN ('in_progress', 'succeeded', 'failed')),
  reviewed_at       timestamptz,                  -- set exactly when review_status = 'succeeded'
  superseded_at     timestamptz,                  -- set when a newer attempt has been reviewed
  chunker_version   text,
  CONSTRAINT submission_attempt_unique UNIQUE (lms_submission_id, attempt),
  CONSTRAINT submission_reviewed_at_matches_status CHECK ((review_status = 'succeeded') = (reviewed_at IS NOT NULL))
);

CREATE INDEX submission_student_assignment_idx ON submission (student_id, assignment_id);

CREATE TABLE observation (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  submission_id uuid NOT NULL REFERENCES submission (id),
  chunk_index   integer NOT NULL CHECK (chunk_index >= 0),
  level         text NOT NULL CHECK (level IN ('sentence', 'paragraph')),
  standard_id   uuid NOT NULL REFERENCES standard (id),
  -- A short reason, 1 to 12 words; never student text.
  reason        text NOT NULL CHECK (word_count(reason) BETWEEN 1 AND 12),
  observed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX observation_submission_idx ON observation (submission_id);
CREATE INDEX observation_standard_idx ON observation (standard_id);

CREATE TABLE finding (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  submission_id     uuid NOT NULL REFERENCES submission (id),
  -- The agent's own words, 1 to 40 words. PRIVACY: never quote submission text.
  summary           text NOT NULL CHECK (word_count(summary) BETWEEN 1 AND 40),
  observation_count integer NOT NULL CHECK (observation_count >= 1),
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX finding_submission_idx ON finding (submission_id);

-- Evidence: each observation supports at most one finding (class diagram: Finding 0..1 -- 1..* Observation).
CREATE TABLE finding_observation (
  finding_id     uuid NOT NULL REFERENCES finding (id),
  observation_id uuid NOT NULL UNIQUE REFERENCES observation (id),
  PRIMARY KEY (finding_id, observation_id)
);

CREATE TABLE finding_standard (
  finding_id  uuid NOT NULL REFERENCES finding (id),
  standard_id uuid NOT NULL REFERENCES standard (id),
  PRIMARY KEY (finding_id, standard_id)
);

-- PROPOSED STUB: owned and written by the backend. Teachers annotate findings; they never edit them.
CREATE TABLE teacher_response (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  finding_id   uuid NOT NULL REFERENCES finding (id),
  teacher_id   text NOT NULL REFERENCES teacher (id),
  stance       text NOT NULL CHECK (stance IN ('agree', 'disagree', 'dismiss')),
  reason       text,
  responded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX teacher_response_finding_idx ON teacher_response (finding_id);
