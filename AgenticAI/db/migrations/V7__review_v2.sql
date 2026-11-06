-- Review v2 output (spec 2026-10-28-agentic-framework-v2-design.md section 2).
-- PRIVACY: still no student text. Every new column on an existing table is nullable or defaulted, so the team
-- backend's current queries keep working. IMMUTABILITY: the app never UPDATEs or DELETEs verdict or finding rows.

-- One verdict per standard the reviewers judged in a submission (about 350 rows per submission).
-- Strengths live here, never in finding: the team's Findings page lists every finding row.
CREATE TABLE verdict (
  submission_id    uuid NOT NULL REFERENCES submission (id),
  standard_id      uuid NOT NULL REFERENCES standard (id),
  verdict          text NOT NULL CHECK (verdict IN ('meets', 'growth_area', 'not_evident')),
  -- Only for a growth area. User-facing words live in display_label.
  support_level    text CHECK (support_level IN ('approaching', 'developing', 'beginning')),
  working_grade    smallint,                     -- the grade level the student's work shows for this standard
  notable_strength boolean NOT NULL DEFAULT false,
  -- The agent's own words, 1 to 40 words. PRIVACY: never quote submission text.
  strength_note    text,
  segments         smallint[],                   -- indexes of the submission segments that were evidence
  confidence       real CHECK (confidence BETWEEN 0 AND 1),
  reviewer         text,                         -- the reviewer id from bands.toml, e.g. 6+-writing
  PRIMARY KEY (submission_id, standard_id),
  CONSTRAINT verdict_support_level_iff_growth_area CHECK ((verdict = 'growth_area') = (support_level IS NOT NULL)),
  CONSTRAINT verdict_strength_note_iff_notable CHECK (
    notable_strength = (strength_note IS NOT NULL)
    AND (strength_note IS NULL OR word_count(strength_note) BETWEEN 1 AND 40)
  )
);

CREATE INDEX verdict_standard_verdict_idx ON verdict (standard_id, verdict);

-- Finding detail. Growth areas only; every column optional.
ALTER TABLE finding
  ADD COLUMN support_level text CHECK (support_level IN ('approaching', 'developing', 'beginning')),
  ADD COLUMN confidence    real CHECK (confidence BETWEEN 0 AND 1),
  ADD COLUMN condition     text,                 -- e.g. "with a prompt"; from conditions.toml
  ADD COLUMN pattern       text CHECK (pattern IN ('consistent', 'occasional')),
  ADD COLUMN skill_state   text CHECK (skill_state IN ('missing', 'misapplied'));

-- Root cause: a finding points at the lower-grade finding it grows from, within one submission.
CREATE TABLE finding_link (
  finding_id      uuid NOT NULL REFERENCES finding (id),
  root_finding_id uuid NOT NULL REFERENCES finding (id),
  -- The agent's own words, 1 to 40 words. PRIVACY: never quote submission text.
  rationale       text NOT NULL CHECK (word_count(rationale) BETWEEN 1 AND 40),
  PRIMARY KEY (finding_id, root_finding_id)
);

CREATE INDEX finding_link_root_idx ON finding_link (root_finding_id);

CREATE FUNCTION finding_link_same_submission()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF (SELECT submission_id FROM finding WHERE id = NEW.finding_id)
     IS DISTINCT FROM (SELECT submission_id FROM finding WHERE id = NEW.root_finding_id) THEN
    RAISE EXCEPTION 'finding_link must join findings of the same submission'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER finding_link_same_submission
  BEFORE INSERT OR UPDATE ON finding_link
  FOR EACH ROW EXECUTE FUNCTION finding_link_same_submission();

-- Observations can now point at a quiz item or a short answer as well as a text chunk.
ALTER TABLE observation DROP CONSTRAINT observation_level_check;
ALTER TABLE observation ADD CONSTRAINT observation_level_check
  CHECK (level IN ('sentence', 'paragraph', 'item', 'answer'));

-- What produced a review, for audit and evaluation.
ALTER TABLE submission
  ADD COLUMN pipeline        text,               -- mvp | v2
  ADD COLUMN submission_type text,
  ADD COLUMN models          jsonb,              -- model id per reviewer
  ADD COLUMN prompt_versions jsonb,
  ADD COLUMN usage           jsonb,              -- input and output tokens per reviewer
  ADD COLUMN settings_hash   text,
  ADD COLUMN tuning_hash     text;

-- Teacher/admin setting changes. APPEND-ONLY BY CONVENTION: the app only INSERTs; the effective value is the
-- latest row per key, and the rows are the history.
CREATE TABLE review_setting_override (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key        text NOT NULL,
  value      jsonb NOT NULL,
  changed_by text NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE review_setting_override IS
  'Append-only by convention: the app never updates or deletes rows. Latest row per key wins; all rows are the history.';

CREATE INDEX review_setting_override_key_idx ON review_setting_override (key, changed_at DESC);

-- The only place user-facing words for codes live.
CREATE TABLE display_label (
  kind  text NOT NULL,
  code  text NOT NULL,
  label text NOT NULL,
  PRIMARY KEY (kind, code)
);

INSERT INTO display_label (kind, code, label) VALUES
  ('verdict',       'meets',            'Meets'),
  ('verdict',       'growth_area',      'Growth area'),
  ('verdict',       'not_evident',      'Not evident'),
  ('support_level', 'approaching',      'Approaching'),
  ('support_level', 'developing',       'Developing'),
  ('support_level', 'beginning',        'Beginning'),
  ('flag',          'notable_strength', 'Strength');
