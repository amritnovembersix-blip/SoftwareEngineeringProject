import pytest
from fakes import clear_standards, essay_row, label_row, std_row, write_essays, write_labels, xlsx_fixture

from agentic.llm.fake import FakeEmbedder
from agentic.models import (Finding, Observation, ReviewResult, StandardRef, SubmissionRef,
                            Verification)
from agentic.results.base import ResultsWriter
from agentic.results.postgres_writer import PostgresResultsWriter
from agentic.results.seed import seed_demo
from agentic.standards.loader import load, normalize_rows, read_xlsx

pytestmark = pytest.mark.db

H = "a" * 64
SECRET = "Secret essay sentence alpha."


@pytest.fixture
def seeded(db_conn, tmp_path):
    write_labels(tmp_path, "1.01", [label_row(student_hash=H, grade=9),
                                    label_row(student_hash="b" * 64, grade=9)])
    write_essays(tmp_path, "1.01", [
        essay_row(student_hash=H, paragraphs=[SECRET, "Beta."], course_id="classroom_1.01",
                  assignment_id="asg-classroom_1.01-essay1"),
        essay_row(student_hash="b" * 64, paragraphs=["Other."], course_id="classroom_1.01",
                  assignment_id="asg-classroom_1.01-essay1", submission_id="sub-2")])
    seed_demo(db_conn, tmp_path, "1.01")
    clear_standards(db_conn)  # the dev DB may hold real standards and saved reviews
    rows = normalize_rows(read_xlsx(xlsx_fixture(tmp_path, [std_row("t.1"), std_row("t.2")])))
    load(db_conn, rows, FakeEmbedder())
    return db_conn, tmp_path


def _result(codes=("t.1", "t.2"), sub="sub-1", attempt=1):
    ref = SubmissionRef(course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1",
                        user_id=H, submission_id=sub, attempt=attempt)
    obs = [Observation(standard_code=codes[0], paragraph=1, reason="Ideas lack supporting detail"),
           Observation(standard_code=codes[0], paragraph=2, reason="Needs more evidence"),
           Observation(standard_code=codes[1], paragraph=2, reason="Transitions are missing")]
    finds = [Finding(standard_code=codes[0], concept="C", description="D", paragraphs=[1, 2],
                     reasons=["a", "b"], observation_count=2, summary="Two paragraphs need work"),
             Finding(standard_code=codes[1], concept="C", description="D", paragraphs=[2],
                     reasons=["a"], observation_count=1, summary="One paragraph needs work")]
    return ReviewResult(submission_ref=ref, course_grade=9, strategy="full", standards=[],
                        observations=obs, findings=finds, met_standards=[], dropped=[],
                        verification=Verification(status="disabled"))


def _n(conn, table, where="true"):
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()[0]


def test_seed_demo_idempotent(db_conn, tmp_path):
    write_labels(tmp_path, "1.01", [label_row(student_hash=H), label_row(student_hash="b" * 64)])
    write_essays(tmp_path, "1.01", [essay_row(student_hash=H, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1"),
                                    essay_row(student_hash="b" * 64, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1")])
    assert seed_demo(db_conn, tmp_path, "1.01") == 2
    assert seed_demo(db_conn, tmp_path, "1.01") == 2
    assert _n(db_conn, "student", f"id IN ('{H}', '{'b' * 64}')") == 2
    assert _n(db_conn, "classroom", "id = 'classroom_1.01' AND grade_level = 9 AND subject = 'ela'") == 1
    assert _n(db_conn, "assignment", "id = 'asg-classroom_1.01-essay1'") == 1
    assert _n(db_conn, "teacher", "id = 'demo-teacher'") == 1


def test_seed_demo_enrolls_every_student_once(db_conn, tmp_path):
    """The Students page lists enrolled students, so they show before any review is saved."""
    students = [H, "b" * 64]
    write_labels(tmp_path, "1.01", [label_row(student_hash=h) for h in students])
    write_essays(tmp_path, "1.01", [essay_row(student_hash=h, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1") for h in students])
    seed_demo(db_conn, tmp_path, "1.01")
    seed_demo(db_conn, tmp_path, "1.01")  # idempotent
    ids = "', '".join(students)
    where = f"classroom_id = 'classroom_1.01' AND student_id IN ('{ids}')"
    assert _n(db_conn, "enrollment", where) == 2
    assert _n(db_conn, "enrollment", where + " AND start_date IS NOT NULL AND end_date IS NULL") == 2
    assert _n(db_conn, "submission", f"student_id IN ('{ids}')") == 0  # seeding reviews nothing


def test_writer_is_a_results_writer(seeded):
    assert isinstance(PostgresResultsWriter(seeded[0]), ResultsWriter)


def test_save_writes_expected_rows(seeded):
    conn, _ = seeded
    out = PostgresResultsWriter(conn).save(_result())
    assert out.status == "written"
    run = conn.execute("SELECT status, submission_count, completed_count, finished_at IS NOT NULL, error "
                       "FROM review_run WHERE id = %s", (out.review_run_id,)).fetchone()
    assert run == ("succeeded", 1, 1, True, None)
    sub = conn.execute("SELECT id, review_status, review_run_id::text, reviewed_at IS NOT NULL, "
                       "chunker_version FROM submission WHERE lms_submission_id = 'sub-1'").fetchone()
    assert sub[1:] == ("succeeded", out.review_run_id, True, "mvp-whole-essay-1")
    sid = sub[0]
    obs = conn.execute("SELECT chunk_index, level, reason FROM observation WHERE submission_id = %s "
                       "ORDER BY chunk_index, reason", (sid,)).fetchall()
    assert [(o[0], o[1]) for o in obs] == [(0, "paragraph"), (1, "paragraph"), (1, "paragraph")]
    fs = conn.execute("SELECT summary, observation_count, "
                      "(SELECT count(*) FROM finding_observation fo WHERE fo.finding_id = f.id), "
                      "(SELECT count(*) FROM finding_standard fs WHERE fs.finding_id = f.id) "
                      "FROM finding f WHERE submission_id = %s ORDER BY observation_count DESC", (sid,)).fetchall()
    assert [(f[1], f[2], f[3]) for f in fs] == [(2, 2, 1), (1, 1, 1)]
    dump = " ".join(str(v) for t in ("review_run", "submission", "observation", "finding")
                    for row in conn.execute(f"SELECT * FROM {t}").fetchall() for v in row)
    assert SECRET not in dump


def test_save_duplicate_attempt(seeded):
    conn, _ = seeded
    w = PostgresResultsWriter(conn)
    first = w.save(_result())
    before = {t: _n(conn, t) for t in ("review_run", "submission", "observation", "finding")}
    second = w.save(_result())
    assert second.status == "duplicate"
    assert second.review_run_id == first.review_run_id
    assert {t: _n(conn, t) for t in before} == before


def test_failed_save_leaves_no_observations(seeded):
    conn, _ = seeded
    runs_before = _n(conn, "review_run")
    with pytest.raises(LookupError):
        PostgresResultsWriter(conn).save(_result(codes=("t.1", "no.such.code")))
    assert _n(conn, "review_run") == runs_before + 1
    run = conn.execute("SELECT status, error FROM review_run ORDER BY created_at DESC LIMIT 1").fetchone()
    assert run == ("failed", "LookupError")
    assert _n(conn, "observation", "submission_id IN (SELECT id FROM submission WHERE lms_submission_id = 'sub-1')") == 0
    assert _n(conn, "submission", "lms_submission_id = 'sub-1' AND review_status = 'succeeded'") == 0


def test_retry_after_failure_succeeds(seeded):
    conn, _ = seeded
    w = PostgresResultsWriter(conn)
    with pytest.raises(LookupError):
        w.save(_result(codes=("t.1", "no.such.code")))
    out = w.save(_result())
    assert out.status == "written"
    assert _n(conn, "submission", "lms_submission_id = 'sub-1' AND review_status = 'succeeded'") == 1


def test_save_records_trigger(seeded):
    conn, _ = seeded
    w = PostgresResultsWriter(conn)
    out = w.save(_result(), trigger="on_grade")
    trigger = conn.execute("SELECT trigger_type FROM review_run WHERE id = %s", (out.review_run_id,)).fetchone()
    assert trigger == ("on_grade",)


def test_is_reviewed(seeded):
    conn, _ = seeded
    w = PostgresResultsWriter(conn)
    assert not w.is_reviewed("sub-1", 1)
    w.save(_result())
    assert w.is_reviewed("sub-1", 1) and not w.is_reviewed("sub-1", 2)


def test_reset_reviews_clears_one_classroom_only(seeded):
    from agentic.results.reset import reset_reviews

    conn, _ = seeded
    out = PostgresResultsWriter(conn).save(_result())
    sid = conn.execute("SELECT id FROM submission WHERE lms_submission_id = 'sub-1'").fetchone()[0]
    fid = conn.execute("SELECT id FROM finding WHERE submission_id = %s LIMIT 1", (sid,)).fetchone()[0]
    conn.execute("INSERT INTO teacher_response (finding_id, teacher_id, stance) VALUES (%s, 'demo-teacher', 'agree')",
                 (fid,))
    assert reset_reviews(conn, "classroom_no_such")["submission"] == 0
    assert _n(conn, "submission", "lms_submission_id = 'sub-1'") == 1

    counts = reset_reviews(conn, "classroom_1.01")
    assert counts["finding"] >= 2 and counts["observation"] >= 3 and counts["teacher_response"] >= 1
    assert _n(conn, "submission", "lms_submission_id = 'sub-1'") == 0
    assert _n(conn, "finding", f"submission_id = '{sid}'") == 0
    assert _n(conn, "review_run", f"id = '{out.review_run_id}'") == 0
    assert _n(conn, "standard", "code IN ('t.1', 't.2')") == 2  # kept
    assert _n(conn, "student", f"id = '{H}'") == 1 and _n(conn, "classroom", "id = 'classroom_1.01'") == 1
    assert _n(conn, "enrollment", f"classroom_id = 'classroom_1.01' AND student_id = '{H}'") == 1  # kept
    assert not PostgresResultsWriter(conn).is_reviewed("sub-1", 1)  # can be reviewed again


def _lms_named_class(conn):
    """A classroom whose SASS keys differ from its LMS IDs (as a backend import might create)."""
    conn.execute("INSERT INTO classroom (id, lms_course_id, teacher_id, subject, grade_level, name) "
                 "VALUES ('sass-class-9', 'lms-course-9', 'demo-teacher', 'ela', 9, 'Class 9')")
    conn.execute("INSERT INTO assignment (id, lms_assignment_id, classroom_id, title, submission_type) "
                 "VALUES ('sass-asg-9', 'lms-asg-9', 'sass-class-9', 'Essay', 'essay')")
    conn.execute("INSERT INTO student (id, lms_user_id) VALUES ('sass-stu-9', 'lms-user-9')")


def _lms_result(user="lms-user-9"):
    r = _result(sub="lms-sub-9")
    ref = r.submission_ref.model_copy(update={"course_id": "lms-course-9", "assignment_id": "lms-asg-9",
                                              "user_id": user})
    return r.model_copy(update={"submission_ref": ref})


def test_save_maps_lms_ids_to_sass_rows(seeded):
    conn, _ = seeded
    _lms_named_class(conn)
    out = PostgresResultsWriter(conn).save(_lms_result())
    assert out.status == "written"
    row = conn.execute("SELECT assignment_id, student_id FROM submission WHERE lms_submission_id = 'lms-sub-9'"
                       ).fetchone()
    assert row == ("sass-asg-9", "sass-stu-9")


def test_save_unseeded_student_fails_cleanly(seeded):
    conn, _ = seeded
    _lms_named_class(conn)
    with pytest.raises(LookupError) as ei:
        PostgresResultsWriter(conn).save(_lms_result(user="lms-user-unknown"))
    assert "lms-user-unknown" not in str(ei.value)
    assert _n(conn, "submission", "lms_submission_id = 'lms-sub-9'") == 0
    assert _n(conn, "review_run", "status = 'failed' AND error = 'LookupError'") >= 1


def test_reset_by_lms_course_id(seeded):
    from agentic.results.reset import reset_reviews

    conn, _ = seeded
    _lms_named_class(conn)
    PostgresResultsWriter(conn).save(_lms_result())
    assert reset_reviews(conn, "sass-class-9")["submission"] == 0  # the SASS key is not the LMS ID
    assert reset_reviews(conn, "lms-course-9")["submission"] == 1


# ---- v2 results ----

def _v2_result(sub="sub-1", extra_verdicts=(), links=None):
    """t.1 and t.2 are growth areas (t.2 grows from t.1); t.3 is a notable strength."""
    from agentic.llm.base import Usage
    from agentic.models import FindingLink, Verdict

    r = _result(sub=sub)
    finds = [f.model_copy(update={"support_level": "developing", "confidence": 0.8, "condition": "with a prompt",
                                  "pattern": "consistent", "skill_state": "missing"}) for f in r.findings]
    verdicts = [
        Verdict(standard_code="t.1", verdict="growth_area", support_level="developing", working_grade=7,
                segments=[0, 1], confidence=0.8, reviewer="6+-writing"),
        Verdict(standard_code="t.2", verdict="growth_area", support_level="beginning", segments=[1],
                reviewer="6+-writing"),
        Verdict(standard_code="t.3", verdict="meets", notable_strength=True, strength_note="Strong opening hook",
                reviewer="6+-writing"),
        *extra_verdicts]
    if links is None:
        links = [FindingLink(finding_code="t.2", root_code="t.1", rationale="Lacks the earlier skill")]
    return r.model_copy(update={
        "pipeline": "v2", "submission_type": "essay", "findings": finds, "verdicts": verdicts, "links": links,
        "usage": {"6+-writing": Usage(input_tokens=100, output_tokens=20, estimated=False)},
        "models": {"6+-writing": "gemini-2.5-flash", "verify": "jev-1"},
        "prompt_versions": {"6+-writing": "v1"}, "settings_hash": "sh", "tuning_hash": "th",
        "trace": {"echo": SECRET}})


@pytest.fixture
def seeded3(seeded):
    conn, tmp = seeded
    clear_standards(conn)
    rows = normalize_rows(read_xlsx(xlsx_fixture(tmp, [std_row("t.1"), std_row("t.2"), std_row("t.3")])))
    load(conn, rows, FakeEmbedder())
    return conn


def _sub_filter():
    return "submission_id IN (SELECT id FROM submission WHERE lms_submission_id = 'sub-1')"


def test_save_v2_writes_new_rows(seeded3):
    conn = seeded3
    assert PostgresResultsWriter(conn).save(_v2_result()).status == "written"
    sid = conn.execute("SELECT id FROM submission WHERE lms_submission_id = 'sub-1'").fetchone()[0]
    sub = conn.execute("SELECT pipeline, submission_type, models, prompt_versions, usage, settings_hash, "
                       "tuning_hash FROM submission WHERE id = %s", (sid,)).fetchone()
    assert sub == ("v2", "essay", {"6+-writing": "gemini-2.5-flash", "verify": "jev-1"}, {"6+-writing": "v1"},
                   {"6+-writing": {"input_tokens": 100, "output_tokens": 20, "estimated": False,
                                    "thinking_tokens": 0, "calls": 0}}, "sh", "th")
    vs = conn.execute("SELECT st.code, v.verdict, v.support_level, v.working_grade, v.notable_strength, "
                      "v.strength_note, v.segments, v.confidence, v.reviewer FROM verdict v "
                      "JOIN standard st ON st.id = v.standard_id WHERE v.submission_id = %s ORDER BY st.code",
                      (sid,)).fetchall()
    assert vs == [("t.1", "growth_area", "developing", 7, False, None, [0, 1], pytest.approx(0.8), "6+-writing"),
                  ("t.2", "growth_area", "beginning", None, False, None, [1], None, "6+-writing"),
                  ("t.3", "meets", None, None, True, "Strong opening hook", [], None, "6+-writing")]
    fs = conn.execute("SELECT st.code, f.support_level, f.confidence, f.condition, f.pattern, f.skill_state "
                      "FROM finding f JOIN finding_standard fs ON fs.finding_id = f.id "
                      "JOIN standard st ON st.id = fs.standard_id WHERE f.submission_id = %s ORDER BY st.code",
                      (sid,)).fetchall()
    assert fs == [("t.1", "developing", pytest.approx(0.8), "with a prompt", "consistent", "missing"),
                  ("t.2", "developing", pytest.approx(0.8), "with a prompt", "consistent", "missing")]
    link = conn.execute("SELECT fst.code, rst.code, l.rationale FROM finding_link l "
                        "JOIN finding_standard fs ON fs.finding_id = l.finding_id "
                        "JOIN standard fst ON fst.id = fs.standard_id "
                        "JOIN finding_standard rs ON rs.finding_id = l.root_finding_id "
                        "JOIN standard rst ON rst.id = rs.standard_id").fetchall()
    assert link == [("t.2", "t.1", "Lacks the earlier skill")]
    dump = " ".join(str(v) for t in ("submission", "verdict", "finding", "finding_link")
                    for row in conn.execute(f"SELECT * FROM {t}").fetchall() for v in row)
    assert SECRET not in dump


def test_save_v2_inserts_findings_whole_and_labels_the_chunker(seeded3):
    conn = seeded3
    statements = []
    real = conn.execute

    def spy(query, *args, **kw):
        statements.append(str(query))
        return real(query, *args, **kw)

    conn.execute = spy
    try:
        PostgresResultsWriter(conn).save(_v2_result())
    finally:
        del conn.execute
    assert any("INSERT INTO finding (submission_id, summary, observation_count, support_level" in s
               for s in statements)
    assert not any(s.lstrip().upper().startswith("UPDATE FINDING") for s in statements)
    assert conn.execute("SELECT chunker_version FROM submission WHERE lms_submission_id = 'sub-1'"
                        ).fetchone()[0] == "v2-segments-1"


def test_save_v2_with_band_strengths_saves(seeded3):
    """C1: a strength flagged without a note is dropped by the band reviewer, so V7's
    verdict_strength_note_iff_notable never fails the save."""
    from agentic.config import PACKAGE_ROOT
    from agentic.llm.fake import FakeChatModel
    from agentic.review.v2.bands import BandReviewer
    from agentic.review.v2.schemas import PerStandardOut, StandardJudgment
    from agentic.tuning import ReviewerSpec, load_prompt

    class _Scope:
        def standards(self):
            return [StandardRef(code=c, concept="C", description="D", grade_min=9, grade_max=10, strand="W")
                    for c in ("t.1", "t.3")]

    chat = FakeChatModel({PerStandardOut: lambda s, u: PerStandardOut(judgments=[
        StandardJudgment(standard_code="t.1", verdict="meets", notable_strength=True),
        StandardJudgment(standard_code="t.3", verdict="meets", notable_strength=True,
                         strength_note="Strong opening hook")])})
    spec = ReviewerSpec(id="w", grades=(3, 5), strands=("W",), mode="per_standard", prompt="per_standard.v1")
    band = BandReviewer(spec, 3, 5, _Scope(), chat, load_prompt(PACKAGE_ROOT / "prompts", "per_standard.v1"))
    res = band.review(["One."], "paragraph", None, True)
    result = _v2_result(links=[]).model_copy(update={"verdicts": res.verdicts, "findings": [],
                                                    "observations": []})
    conn = seeded3
    assert PostgresResultsWriter(conn).save(result).status == "written"
    rows = conn.execute("SELECT st.code, v.notable_strength, v.strength_note FROM verdict v "
                        "JOIN standard st ON st.id = v.standard_id JOIN submission s ON s.id = v.submission_id "
                        "WHERE s.lms_submission_id = 'sub-1' ORDER BY st.code").fetchall()
    assert rows == [("t.1", False, None), ("t.3", True, "Strong opening hook")]


def test_save_mvp_leaves_new_columns_null(seeded3):
    conn = seeded3
    PostgresResultsWriter(conn).save(_result())
    row = conn.execute("SELECT pipeline, usage FROM submission WHERE lms_submission_id = 'sub-1'").fetchone()
    assert row == (None, None)
    assert _n(conn, "verdict", _sub_filter()) == 0


def test_v2_unknown_verdict_code_raises_before_any_write(seeded3):
    from agentic.models import Verdict

    conn = seeded3
    bad = Verdict(standard_code="no.such", verdict="meets", reviewer="r")
    with pytest.raises(LookupError):
        PostgresResultsWriter(conn).save(_v2_result(extra_verdicts=[bad]))
    assert _n(conn, "verdict", _sub_filter()) == 0 and _n(conn, "observation", _sub_filter()) == 0
    assert _n(conn, "finding", _sub_filter()) == 0
    assert _n(conn, "submission", "lms_submission_id = 'sub-1' AND review_status = 'succeeded'") == 0


def test_v2_failed_link_rolls_back_everything(seeded3):
    from agentic.models import FindingLink

    conn = seeded3
    bad = [FindingLink(finding_code="t.2", root_code="t.3", rationale="No such finding")]  # t.3 has no finding
    with pytest.raises(LookupError):
        PostgresResultsWriter(conn).save(_v2_result(links=bad))
    assert _n(conn, "verdict", _sub_filter()) == 0 and _n(conn, "finding", _sub_filter()) == 0
    assert _n(conn, "submission", "lms_submission_id = 'sub-1' AND review_status = 'succeeded'") == 0


def test_reset_removes_verdicts_and_links(seeded3):
    from agentic.results.reset import reset_reviews

    conn = seeded3
    PostgresResultsWriter(conn).save(_v2_result())
    counts = reset_reviews(conn, "classroom_1.01")
    assert counts["verdict"] == 3 and counts["finding_link"] == 1 and counts["submission"] == 1
    assert _n(conn, "verdict", "submission_id IN (SELECT id FROM submission)") == 0
    assert _n(conn, "finding_link", "finding_id IN (SELECT id FROM finding)") == 0


def test_v2_band_findings_link_exactly_their_own_observations(seeded3):
    conn = seeded3
    PostgresResultsWriter(conn).save(_v2_result())
    rows = conn.execute("SELECT fst.code, o.chunk_index, ost.code FROM finding_observation fo "
                        "JOIN finding_standard fs ON fs.finding_id = fo.finding_id "
                        "JOIN standard fst ON fst.id = fs.standard_id "
                        "JOIN observation o ON o.id = fo.observation_id "
                        "JOIN standard ost ON ost.id = o.standard_id ORDER BY 1, 2").fetchall()
    assert rows == [("t.1", 0, "t.1"), ("t.1", 1, "t.1"), ("t.2", 1, "t.2")]


def test_v2_concept_finding_links_picks_on_every_standard_of_the_concept(seeded):
    conn, tmp = seeded
    clear_standards(conn)
    codes = ["3.R.1.B.a", "3.R.1.B.b", "3.R.1.C.a"]
    load(conn, normalize_rows(read_xlsx(xlsx_fixture(tmp, [std_row(c) for c in codes]))), FakeEmbedder())
    r = _result(codes=codes[:2])
    obs = [Observation(standard_code=codes[0], paragraph=1, reason="mixes up claim", level="item"),
           Observation(standard_code=codes[1], paragraph=2, reason="mixes up evidence", level="item"),
           Observation(standard_code=codes[2], paragraph=3, reason="other concept", level="item")]
    finding = Finding(standard_code=codes[0], concept="R.1.B", description="", paragraphs=[1, 2],
                      reasons=["a", "b"], observation_count=2, summary="Two picks", severity="beginning",
                      support_level="beginning", confidence=0.9)
    r = r.model_copy(update={"pipeline": "v2", "submission_type": "multiple_choice", "observations": obs,
                             "findings": [finding], "verdicts": [], "links": []})
    PostgresResultsWriter(conn).save(r)
    linked = conn.execute("SELECT o.chunk_index, st.code FROM finding_observation fo "
                          "JOIN observation o ON o.id = fo.observation_id "
                          "JOIN standard st ON st.id = o.standard_id ORDER BY 1").fetchall()
    assert linked == [(0, "3.R.1.B.a"), (1, "3.R.1.B.b")]


def test_v2_band_findings_in_one_concept_link_only_their_own_observations(seeded):
    conn, tmp = seeded
    clear_standards(conn)
    codes = ["3.L.1.A.e", "3.L.1.A.g"]
    load(conn, normalize_rows(read_xlsx(xlsx_fixture(tmp, [std_row(c) for c in codes]))), FakeEmbedder())
    obs = [Observation(standard_code=c, paragraph=1, reason="band reason", level="paragraph") for c in codes]
    finds = [Finding(standard_code=c, concept="L.1.A", description="", paragraphs=[1], reasons=["a"],
                     observation_count=1, summary="One paragraph", severity="developing",
                     support_level="developing", confidence=0.8) for c in codes]
    r = _result(codes=codes).model_copy(update={"pipeline": "v2", "submission_type": "essay",
                                                "observations": obs, "findings": finds,
                                                "verdicts": [], "links": []})
    PostgresResultsWriter(conn).save(r)
    rows = conn.execute("SELECT fst.code, ost.code FROM finding_observation fo "
                        "JOIN finding_standard fs ON fs.finding_id = fo.finding_id "
                        "JOIN standard fst ON fst.id = fs.standard_id "
                        "JOIN observation o ON o.id = fo.observation_id "
                        "JOIN standard ost ON ost.id = o.standard_id ORDER BY 1").fetchall()
    assert rows == [("3.L.1.A.e", "3.L.1.A.e"), ("3.L.1.A.g", "3.L.1.A.g")]


def test_v2_sentence_level_observations_are_saved(seeded):
    conn, tmp = seeded
    clear_standards(conn)
    codes = ["3.L.1.A.e", "3.L.1.A.g"]
    load(conn, normalize_rows(read_xlsx(xlsx_fixture(tmp, [std_row(c) for c in codes]))), FakeEmbedder())
    obs = [Observation(standard_code=codes[0], paragraph=4, reason="band reason", level="sentence")]
    r = _result(codes=codes).model_copy(update={"pipeline": "v2", "submission_type": "essay",
                                                "observations": obs, "findings": [],
                                                "verdicts": [], "links": []})
    assert PostgresResultsWriter(conn).save(r).status == "written"
    rows = conn.execute("SELECT o.chunk_index, o.level FROM observation o JOIN submission s "
                        "ON s.id = o.submission_id WHERE s.lms_submission_id = 'sub-1'").fetchall()
    assert rows == [(3, "sentence")]
