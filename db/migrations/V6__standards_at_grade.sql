-- MVP grade scope: a course at grade G is held to the standards whose grade band contains G
-- (grade 9 -> the 9-10 band only). standards_in_scope (G and every lower grade) is unchanged.
-- Retrieval: SELECT ... FROM standards_at_grade(:g) WHERE embedding IS NOT NULL ORDER BY embedding <=> :q LIMIT :k
CREATE FUNCTION standards_at_grade(course_grade integer)
RETURNS SETOF standard
LANGUAGE sql STABLE
AS $$
  SELECT * FROM standard WHERE grade_min <= course_grade AND course_grade <= grade_max
$$;
