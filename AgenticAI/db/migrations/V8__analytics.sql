-- Class summary, trends and statistics output (spec 2026-11-02-trends-class-summary-design.md section 5).
-- PRIVACY: no table here stores student text; only ids, concept codes, levels, counts and dates.
-- IMMUTABILITY: every analysis run INSERTs new snapshot rows under its own review_run (run_type = 'trend_analysis').
-- The app never UPDATEs or DELETEs them. The only UPDATE is the Backend's pairing decision on peer_pair_suggestion.
-- A run's review_run.scope is {"scope": "classroom"|"student", "classroom_id": "<id>", "student_id": "<id>"|null,
-- "as_of": "<iso>"}. The Backend reads the *_latest views.

-- One row per student and concept for one analysis run.
CREATE TABLE concept_status (
  review_run_id uuid NOT NULL REFERENCES review_run (id),
  student_id    text NOT NULL REFERENCES student (id),
  classroom_id  text NOT NULL REFERENCES classroom (id),
  concept_code  text NOT NULL,                   -- the first four segments of a standard code, e.g. 7.W.1.A
  status        text NOT NULL CHECK (status IN ('needs_attention', 'secure', 'emerging', 'too_little_evidence')),
  current_level smallint NOT NULL CHECK (current_level BETWEEN 0 AND 3),   -- 0 Meets .. 3 Beginning
  -- A count string "<hits>/<window>", never a percentage.
  basis         text NOT NULL CHECK (basis ~ '^\d+/\d+$'),
  points        smallint[] NOT NULL CHECK (points <@ ARRAY[0, 1, 2, 3]::smallint[]),   -- the evidence levels, oldest first
  trend         text NOT NULL CHECK (trend IN ('improving', 'steady', 'declining', 'unclear')),
  as_of         timestamptz NOT NULL,
  PRIMARY KEY (review_run_id, student_id, concept_code)
);

CREATE INDEX concept_status_student_concept_idx ON concept_status (student_id, concept_code);
CREATE INDEX concept_status_classroom_idx ON concept_status (classroom_id);

-- How many students sat at each level for a concept in a week, for one analysis run.
CREATE TABLE class_concept_week (
  review_run_id uuid NOT NULL REFERENCES review_run (id),
  classroom_id  text NOT NULL REFERENCES classroom (id),
  concept_code  text NOT NULL,
  week_start    date NOT NULL,
  n_level0      integer NOT NULL CHECK (n_level0 >= 0),
  n_level1      integer NOT NULL CHECK (n_level1 >= 0),
  n_level2      integer NOT NULL CHECK (n_level2 >= 0),
  n_level3      integer NOT NULL CHECK (n_level3 >= 0),
  n_too_little  integer NOT NULL CHECK (n_too_little >= 0),   -- students with too little evidence to place
  PRIMARY KEY (review_run_id, classroom_id, concept_code, week_start)
);

-- Students who share a lower-grade root of a grade-level growth area.
CREATE TABLE reteach_group (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  review_run_id uuid NOT NULL REFERENCES review_run (id),
  classroom_id  text NOT NULL REFERENCES classroom (id),
  root_concept  text NOT NULL,
  grade_concept text NOT NULL
);

CREATE INDEX reteach_group_run_idx ON reteach_group (review_run_id, classroom_id);

CREATE TABLE reteach_group_member (
  group_id   uuid NOT NULL REFERENCES reteach_group (id),
  student_id text NOT NULL REFERENCES student (id),
  PRIMARY KEY (group_id, student_id)
);

-- Peer-tutoring suggestions. Unlike the snapshots above, one row lives across runs so the teacher's decision
-- sticks. The Backend updates status, decided_by and decided_at (teacher approval, FR7).
CREATE TABLE peer_pair_suggestion (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  classroom_id text NOT NULL REFERENCES classroom (id),
  concept_code text NOT NULL,
  tutor_id     text NOT NULL REFERENCES student (id),
  tutee_id     text NOT NULL REFERENCES student (id),
  status       text NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested', 'approved', 'rejected')),
  decided_by   text,                             -- the deciding teacher's id
  decided_at   timestamptz,
  CONSTRAINT peer_pair_unique UNIQUE (classroom_id, concept_code, tutor_id, tutee_id),
  CONSTRAINT peer_pair_distinct_students CHECK (tutor_id <> tutee_id),
  CONSTRAINT peer_pair_decided_at_matches_status CHECK ((status = 'suggested') = (decided_at IS NULL))
);

-- Append-only: which run suggested which pair, so a re-suggested pair keeps the teacher's earlier decision.
CREATE TABLE peer_pair_seen (
  review_run_id uuid NOT NULL REFERENCES review_run (id),
  pair_id       uuid NOT NULL REFERENCES peer_pair_suggestion (id),
  PRIMARY KEY (review_run_id, pair_id)
);

CREATE INDEX peer_pair_seen_pair_idx ON peer_pair_seen (pair_id);

COMMENT ON TABLE concept_status IS 'Append-only snapshot per analysis run. Never UPDATEd or DELETEd. No student text.';
COMMENT ON COLUMN concept_status.basis IS 'Count string "<hits>/<window>", never a percentage.';
COMMENT ON TABLE class_concept_week IS 'Append-only snapshot per analysis run. Never UPDATEd or DELETEd. No student text.';
COMMENT ON TABLE reteach_group IS 'Append-only snapshot per analysis run. Never UPDATEd or DELETEd. No student text.';
COMMENT ON TABLE reteach_group_member IS 'Append-only snapshot per analysis run. Never UPDATEd or DELETEd. No student text.';
COMMENT ON TABLE peer_pair_suggestion IS
  'One row per pair across runs. The only UPDATE in the analytics tables is the Backend''s decision (status, decided_by, decided_at). No student text.';
COMMENT ON TABLE peer_pair_seen IS 'Append-only: records which run suggested which pair. Never UPDATEd or DELETEd.';

-- Latest views: only successful trend-analysis runs count, newest finished_at first, ties broken by run id.

-- NOTE: a saved past-dated (as_of) run becomes the latest if it finishes last.
CREATE VIEW concept_status_latest AS
SELECT DISTINCT ON (cs.student_id, cs.classroom_id, cs.concept_code)
       cs.review_run_id, cs.student_id, cs.classroom_id, cs.concept_code, cs.status, cs.current_level,
       cs.basis, cs.points, cs.trend, cs.as_of
FROM concept_status cs
JOIN review_run r ON r.id = cs.review_run_id
WHERE r.run_type = 'trend_analysis' AND r.status = 'succeeded'
ORDER BY cs.student_id, cs.classroom_id, cs.concept_code, r.finished_at DESC, r.id DESC;

CREATE VIEW class_concept_week_latest AS
SELECT DISTINCT ON (w.classroom_id, w.concept_code, w.week_start)
       w.review_run_id, w.classroom_id, w.concept_code, w.week_start,
       w.n_level0, w.n_level1, w.n_level2, w.n_level3, w.n_too_little
FROM class_concept_week w
JOIN review_run r ON r.id = w.review_run_id
WHERE r.run_type = 'trend_analysis' AND r.status = 'succeeded'
ORDER BY w.classroom_id, w.concept_code, w.week_start, r.finished_at DESC, r.id DESC;

-- The newest classroom-scope analysis run per classroom. Groups and pairs come from this run only.
CREATE VIEW classroom_run_latest AS
SELECT DISTINCT ON (r.scope->>'classroom_id')
       r.scope->>'classroom_id' AS classroom_id, r.id AS review_run_id
FROM review_run r
WHERE r.run_type = 'trend_analysis' AND r.status = 'succeeded' AND r.scope->>'scope' = 'classroom'
ORDER BY r.scope->>'classroom_id', r.finished_at DESC, r.id DESC;

-- One row per group member.
CREATE VIEW reteach_group_latest AS
SELECT g.id AS group_id, g.review_run_id, g.classroom_id, g.root_concept, g.grade_concept, m.student_id
FROM classroom_run_latest l
JOIN reteach_group g ON g.review_run_id = l.review_run_id AND g.classroom_id = l.classroom_id
JOIN reteach_group_member m ON m.group_id = g.id;

CREATE VIEW peer_pair_latest AS
SELECT p.id AS pair_id, l.review_run_id, p.classroom_id, p.concept_code, p.tutor_id, p.tutee_id,
       p.status, p.decided_by, p.decided_at
FROM classroom_run_latest l
JOIN peer_pair_seen s ON s.review_run_id = l.review_run_id
JOIN peer_pair_suggestion p ON p.id = s.pair_id AND p.classroom_id = l.classroom_id;
