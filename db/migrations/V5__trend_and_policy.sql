-- PLANNED: trend insights (student or classroom scope). Immutable; each analysis adds a new row.
CREATE TABLE trend_insight (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope         text NOT NULL CHECK (scope IN ('student', 'classroom')),
  student_id    text REFERENCES student (id),
  classroom_id  text REFERENCES classroom (id),
  review_run_id uuid NOT NULL REFERENCES review_run (id),
  summary       text NOT NULL,
  window_start  timestamptz NOT NULL,
  window_end    timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT trend_insight_window CHECK (window_end >= window_start),
  CONSTRAINT trend_insight_scope_target CHECK (
    (scope = 'student'   AND student_id IS NOT NULL AND classroom_id IS NULL) OR
    (scope = 'classroom' AND classroom_id IS NOT NULL AND student_id IS NULL)
  )
);

CREATE TABLE trend_insight_standard (
  trend_insight_id uuid NOT NULL REFERENCES trend_insight (id),
  standard_id      uuid NOT NULL REFERENCES standard (id),
  PRIMARY KEY (trend_insight_id, standard_id)
);

-- A single system-wide review policy (the ReviewPolicy shape from contracts/agentic-api.yaml).
-- Written only through PUT /review-policy. Defaults come from .env until a row exists.
CREATE TABLE review_policy (
  id         smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  policy     jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
