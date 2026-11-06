-- Missouri Learning Standards corpus (docs/curr-mls-standards-ela-2-12-sboe-2016.xlsx).
-- Loaded and embedded by the agentic service. 539 rows for ELA; codes are unique.

CREATE TABLE standard (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code            text NOT NULL UNIQUE,          -- e.g. 3.L.1.A.a
  grade           text NOT NULL,                 -- spreadsheet label: '3' .. '8', '9 to 10', '11 to 12'
  grade_min       smallint NOT NULL,
  grade_max       smallint NOT NULL,
  strand_code     text NOT NULL,                 -- R, RL, RI, RF, W, L, SL (text until the team settles the enum)
  strand          text NOT NULL,
  big_idea        text,
  concept         text,
  description     text NOT NULL,
  sub_expectation text,
  -- DIMENSION IS A PLACEHOLDER: must equal EMBEDDING_DIM for the chosen embedding model.
  -- Changing it = new migration + re-embed (539 rows; cheap).
  embedding       vector(768),
  CONSTRAINT standard_grade_range CHECK (grade_min BETWEEN 0 AND 12 AND grade_max BETWEEN grade_min AND 12)
);

CREATE INDEX standard_grade_min_idx ON standard (grade_min);
-- Deliberately NO approximate (HNSW/IVFFlat) index on embedding. An ANN index applies the
-- grade filter AFTER its scan, so a course whose in-scope standards are rare can get fewer
-- than k results, or none (reproduced in db/tests/verify_schema.sql section 13). At ~539 rows an
-- exact scan is fast. If the corpus grows large, add HNSW together with
-- `SET hnsw.iterative_scan = relaxed_order` and re-run that test.

-- The single definition of the standards boundary: a course at grade G is held to grade G
-- and every lower grade (this catches regression). A band such as 9-10 is in scope once G >= 9.
-- Retrieval: SELECT ... FROM standards_in_scope(:g) ORDER BY embedding <=> :q LIMIT :k
CREATE FUNCTION standards_in_scope(course_grade integer)
RETURNS SETOF standard
LANGUAGE sql STABLE
AS $$
  SELECT * FROM standard WHERE grade_min <= course_grade
$$;
