-- Schema invariant tests. Runs in a transaction and rolls back; leaves no data behind.
-- Run: docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/tests/verify_schema.sql
BEGIN;

-- Helpers --------------------------------------------------------------------
CREATE FUNCTION pg_temp.expect_error(stmt text, expected_state text, label text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = expected_state THEN
      RAISE NOTICE 'PASS: %', label;
      RETURN;
    END IF;
    RAISE EXCEPTION 'FAIL: % (expected SQLSTATE %, got %: %)', label, expected_state, SQLSTATE, SQLERRM;
  END;
  RAISE EXCEPTION 'FAIL: % (statement succeeded; expected SQLSTATE %)', label, expected_state;
END $$;

CREATE FUNCTION pg_temp.expect_count(query text, expected bigint, label text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE actual bigint;
BEGIN
  EXECUTE query INTO actual;
  IF actual IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'FAIL: % (expected %, got %)', label, expected, actual;
  END IF;
  RAISE NOTICE 'PASS: %', label;
END $$;

-- 1. Extension -----------------------------------------------------------------
SELECT pg_temp.expect_count($q$SELECT count(*) FROM pg_extension WHERE extname = 'vector'$q$, 1,
  'pgvector extension installed');

-- Fixtures ---------------------------------------------------------------------
INSERT INTO teacher (id, name) VALUES ('t1', 'Test Teacher');
INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name)
  VALUES ('c1', 'lms-c1', 't1', 'ela', 7, 'ELA 7');
INSERT INTO student (id, lms_user_id) VALUES ('s1', 'lms-u1');
INSERT INTO assignment (id, lms_assignment_id, classroom_id, title, submission_type)
  VALUES ('a1', 'lms-a1', 'c1', 'Essay 1', 'essay');
INSERT INTO review_run (id, run_type, trigger_type)
  VALUES ('00000000-0000-0000-0000-000000000001', 'submission_review', 'on_grade');
INSERT INTO standard (code, grade, grade_min, grade_max, strand_code, strand, description) VALUES
  ('3.W.T.A.a',     '3',        3,  3,  'W', 'Writing', 'g3'),
  ('7.W.T.A.a',     '7',        7,  7,  'W', 'Writing', 'g7'),
  ('8.W.T.A.a',     '8',        8,  8,  'W', 'Writing', 'g8'),
  ('9-10.W.T.A.a',  '9 to 10',  9,  10, 'W', 'Writing', 'g9-10'),
  ('11-12.W.T.A.a', '11 to 12', 11, 12, 'W', 'Writing', 'g11-12');

-- 2. Submissions: resubmission allowed, duplicate attempt rejected ------------------
INSERT INTO submission (id, lms_submission_id, attempt, assignment_id, student_id, submitted_at, review_run_id)
  VALUES ('00000000-0000-0000-0000-0000000000a1', 'lms-sub-1', 1, 'a1', 's1', now(), '00000000-0000-0000-0000-000000000001');
INSERT INTO submission (lms_submission_id, attempt, assignment_id, student_id, submitted_at, review_run_id)
  VALUES ('lms-sub-1', 2, 'a1', 's1', now(), '00000000-0000-0000-0000-000000000001');
SELECT pg_temp.expect_error($q$
  INSERT INTO submission (lms_submission_id, attempt, assignment_id, student_id, submitted_at, review_run_id)
  VALUES ('lms-sub-1', 1, 'a1', 's1', now(), '00000000-0000-0000-0000-000000000001')$q$,
  '23505', 'same (lms_submission_id, attempt) rejected');
SELECT pg_temp.expect_error($q$
  INSERT INTO submission (lms_submission_id, attempt, assignment_id, student_id, submitted_at, review_run_id)
  VALUES ('lms-sub-2', 0, 'a1', 's1', now(), '00000000-0000-0000-0000-000000000001')$q$,
  '23514', 'attempt 0 rejected');

-- 3. Privacy: no student-text column on submission ---------------------------------
SELECT pg_temp.expect_count($q$
  SELECT count(*) FROM information_schema.columns
  WHERE table_name = 'submission' AND column_name IN ('body', 'text', 'content', 'submission_text')$q$,
  0, 'submission has no student-text column');

-- 4. Observation reason: at most 12 words, not blank ---------------------------------
INSERT INTO observation (id, submission_id, chunk_index, level, standard_id, reason)
  SELECT '00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 0, 'sentence', id,
         '  one two three four five six seven eight nine ten eleven twelve  '
  FROM standard WHERE code = '7.W.T.A.a';
INSERT INTO observation (id, submission_id, chunk_index, level, standard_id, reason)
  SELECT '00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a1', 1, 'paragraph', id, 'short reason'
  FROM standard WHERE code = '7.W.T.A.a';
SELECT pg_temp.expect_error($q$
  INSERT INTO observation (submission_id, chunk_index, level, standard_id, reason)
  SELECT '00000000-0000-0000-0000-0000000000a1', 2, 'sentence', id,
         'one two three four five six seven eight nine ten eleven twelve thirteen'
  FROM standard WHERE code = '7.W.T.A.a'$q$,
  '23514', '13-word reason rejected');
SELECT pg_temp.expect_error($q$
  INSERT INTO observation (submission_id, chunk_index, level, standard_id, reason)
  SELECT '00000000-0000-0000-0000-0000000000a1', 3, 'sentence', id, '   '
  FROM standard WHERE code = '7.W.T.A.a'$q$,
  '23514', 'blank reason rejected');
SELECT pg_temp.expect_error($q$
  INSERT INTO observation (submission_id, chunk_index, level, standard_id, reason)
  SELECT '00000000-0000-0000-0000-0000000000a1', 4, 'section', id, 'bad level'
  FROM standard WHERE code = '7.W.T.A.a'$q$,
  '23514', 'unknown chunk level rejected');

-- 5. Evidence: an observation belongs to at most one finding ---------------------------
INSERT INTO finding (id, submission_id, summary, observation_count) VALUES
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'f1', 1),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a1', 'f2', 1);
INSERT INTO finding_observation (finding_id, observation_id)
  VALUES ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000b1');
SELECT pg_temp.expect_error($q$
  INSERT INTO finding_observation (finding_id, observation_id)
  VALUES ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000b1')$q$,
  '23505', 'observation linked to a second finding rejected');

-- 6. Standards scope: course grade G -> every standard with grade_min <= G ----------------
--    (count only this section's fixture codes: the dev DB may hold the real corpus)
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_in_scope(7) WHERE code LIKE '%.W.T.A.a'$q$,  2, 'grade 7: grades 3 and 7 only');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_in_scope(8) WHERE code LIKE '%.W.T.A.a'$q$,  3, 'grade 8: excludes the 9-10 band');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_in_scope(9) WHERE code LIKE '%.W.T.A.a'$q$,  4, 'grade 9: includes the 9-10 band');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_in_scope(10) WHERE code LIKE '%.W.T.A.a'$q$, 4, 'grade 10: excludes the 11-12 band');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_in_scope(12) WHERE code LIKE '%.W.T.A.a'$q$, 5, 'grade 12: all');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_in_scope(2) WHERE code LIKE '%.W.T.A.a'$q$,  0, 'grade 2: no standards in scope');
SELECT pg_temp.expect_error($q$
  INSERT INTO standard (code, grade, grade_min, grade_max, strand_code, strand, description)
  VALUES ('bad', '10', 10, 9, 'W', 'Writing', 'inverted band')$q$,
  '23514', 'grade_min > grade_max rejected');

-- 7. Trend insight scope target -----------------------------------------------------------
SELECT pg_temp.expect_error($q$
  INSERT INTO trend_insight (scope, review_run_id, summary, window_start, window_end)
  VALUES ('student', '00000000-0000-0000-0000-000000000001', 's', now(), now())$q$,
  '23514', 'student-scope insight without student_id rejected');
SELECT pg_temp.expect_error($q$
  INSERT INTO trend_insight (scope, student_id, classroom_id, review_run_id, summary, window_start, window_end)
  VALUES ('classroom', 's1', 'c1', '00000000-0000-0000-0000-000000000001', 's', now(), now())$q$,
  '23514', 'classroom-scope insight that also names a student rejected');
INSERT INTO trend_insight (scope, student_id, review_run_id, summary, window_start, window_end)
  VALUES ('student', 's1', '00000000-0000-0000-0000-000000000001', 'ok', now() - interval '30 days', now());

-- 8. Review policy is a single row --------------------------------------------------------
INSERT INTO review_policy (policy) VALUES ('{}'::jsonb);
SELECT pg_temp.expect_error($q$INSERT INTO review_policy (id, policy) VALUES (2, '{}'::jsonb)$q$,
  '23514', 'second policy row (id 2) rejected');
SELECT pg_temp.expect_error($q$INSERT INTO review_policy (policy) VALUES ('{}'::jsonb)$q$,
  '23505', 'duplicate policy row rejected');

-- 9. Enumerations ---------------------------------------------------------------------------
SELECT pg_temp.expect_error($q$
  INSERT INTO review_run (run_type, trigger_type, status) VALUES ('submission_review', 'on_grade', 'done')$q$,
  '23514', 'unknown run status rejected');
SELECT pg_temp.expect_error($q$
  INSERT INTO teacher_response (finding_id, teacher_id, stance)
  VALUES ('00000000-0000-0000-0000-0000000000c1', 't1', 'maybe')$q$,
  '23514', 'unknown teacher stance rejected');

-- 10. Reason whitespace: tabs/newlines are whitespace too (final review, Important 1) --------------
SELECT pg_temp.expect_error($q$
  INSERT INTO observation (submission_id, chunk_index, level, standard_id, reason)
  SELECT '00000000-0000-0000-0000-0000000000a1', 5, 'sentence', id, E'\t'
  FROM standard WHERE code = '7.W.T.A.a'$q$,
  '23514', 'tab-only reason rejected');
SELECT pg_temp.expect_error($q$
  INSERT INTO observation (submission_id, chunk_index, level, standard_id, reason)
  SELECT '00000000-0000-0000-0000-0000000000a1', 6, 'sentence', id, E'\n'
  FROM standard WHERE code = '7.W.T.A.a'$q$,
  '23514', 'newline-only reason rejected');
INSERT INTO observation (submission_id, chunk_index, level, standard_id, reason)
  SELECT '00000000-0000-0000-0000-0000000000a1', 7, 'sentence', id,
         E'one two three four five six seven eight nine ten eleven twelve\n'
  FROM standard WHERE code = '7.W.T.A.a';
SELECT pg_temp.expect_count($q$SELECT count(*) FROM observation WHERE chunk_index = 7$q$, 1,
  '12-word reason with trailing newline accepted');

-- 11. Free-text caps: the fields most likely to leak student text (final review, Important 4) ------
SELECT pg_temp.expect_error($q$
  INSERT INTO finding (submission_id, summary, observation_count)
  VALUES ('00000000-0000-0000-0000-0000000000a1', repeat('word ', 41), 1)$q$,
  '23514', '41-word finding summary rejected');
SELECT pg_temp.expect_error($q$
  INSERT INTO finding (submission_id, summary, observation_count)
  VALUES ('00000000-0000-0000-0000-0000000000a1', E' \t\n', 1)$q$,
  '23514', 'blank finding summary rejected');
SELECT pg_temp.expect_error($q$
  INSERT INTO review_run (run_type, trigger_type, error)
  VALUES ('submission_review', 'on_grade', repeat('x', 501))$q$,
  '23514', 'run error over 500 chars rejected');

-- 12. Review status for retries (final review, Important 2) ---------------------------------------
SELECT pg_temp.expect_count($q$SELECT count(*) FROM submission WHERE review_status = 'in_progress'$q$, 2,
  'new submission rows default to in_progress');
SELECT pg_temp.expect_error($q$
  INSERT INTO submission (lms_submission_id, attempt, assignment_id, student_id, submitted_at, review_run_id, review_status)
  VALUES ('lms-sub-3', 1, 'a1', 's1', now(), '00000000-0000-0000-0000-000000000001', 'succeeded')$q$,
  '23514', 'succeeded without reviewed_at rejected');
SELECT pg_temp.expect_error($q$
  INSERT INTO submission (lms_submission_id, attempt, assignment_id, student_id, submitted_at, review_run_id, review_status, reviewed_at)
  VALUES ('lms-sub-3', 1, 'a1', 's1', now(), '00000000-0000-0000-0000-000000000001', 'failed', now())$q$,
  '23514', 'failed with reviewed_at rejected');

-- 13. Grade-filtered retrieval returns k results even when in-scope rows are rare -----------------
--     (final review, Important 3: an ANN index filters AFTER the scan and can return too few.)
INSERT INTO standard (code, grade, grade_min, grade_max, strand_code, strand, description, embedding)
  SELECT 'bulk.' || g, '11 to 12', 11, 12, 'W', 'Writing', 'bulk',
         (SELECT array_agg(random())::vector(768) FROM generate_series(1, 768) WHERE g IS NOT NULL)
  FROM generate_series(1, 500) g;
INSERT INTO standard (code, grade, grade_min, grade_max, strand_code, strand, description, embedding)
  SELECT 'low.' || g, '3', 3, 3, 'W', 'Writing', 'low',
         (SELECT array_agg(random())::vector(768) FROM generate_series(1, 768) WHERE g IS NOT NULL)
  FROM generate_series(1, 5) g;
ANALYZE standard;
-- Force the planner off exact paths so any ANN index would be used (rolled back with the test).
DROP INDEX IF EXISTS standard_grade_min_idx;
SET LOCAL enable_seqscan = off;
SELECT pg_temp.expect_count($q$
  SELECT count(*) FROM (
    SELECT code FROM standards_in_scope(3) WHERE code LIKE 'low.%' OR code LIKE 'bulk.%'
    ORDER BY embedding <=> (SELECT array_agg(random())::vector(768) FROM generate_series(1, 768))
    LIMIT 5) t WHERE code LIKE 'low.%'$q$,
  5, 'grade-3 retrieval returns all 5 in-scope embedded standards among 500 out-of-scope');

-- 14. standards_at_grade: the course grade must fall inside the standard's band --------------------
--     (MVP scope; standards_in_scope is unchanged.) Filter on our own codes: earlier sections add rows.
INSERT INTO standard (code, grade, grade_min, grade_max, strand_code, strand, description) VALUES
  ('g14.7',     '7',        7,  7,  'W', 'Writing', 'g14 g7'),
  ('g14.8',     '8',        8,  8,  'W', 'Writing', 'g14 g8'),
  ('g14.9-10',  '9 to 10',  9,  10, 'W', 'Writing', 'g14 g9-10'),
  ('g14.11-12', '11 to 12', 11, 12, 'W', 'Writing', 'g14 g11-12');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_at_grade(9) WHERE code LIKE 'g14.%'$q$, 1,
  'grade 9: only the 9-10 band');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_at_grade(10) WHERE code LIKE 'g14.%'$q$, 1,
  'grade 10: only the 9-10 band');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_at_grade(8) WHERE code LIKE 'g14.%'$q$, 1,
  'grade 8: only the 8-8 standard (no 7, no 9-10)');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_at_grade(12) WHERE code LIKE 'g14.%'$q$, 1,
  'grade 12: only the 11-12 band');
SELECT pg_temp.expect_count($q$SELECT count(*) FROM standards_at_grade(2) WHERE code LIKE 'g14.%'$q$, 0,
  'grade 2: nothing');

SELECT 'ALL SCHEMA CHECKS PASSED' AS result;
ROLLBACK;
