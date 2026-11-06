"""Live smoke tests: call real external APIs. Run with `-m live`; each skips when its key is empty."""
from pathlib import Path

import psycopg
import pytest

from agentic import wiring
from agentic.config import Settings
from agentic.llm.fake import FakeEmbedder
from agentic.models import Finding, MetStandard
from agentic.review.strategies import FullListStrategy
from agentic.review.v2.options import ReviewOptions
from agentic.review.v2.run import review_one
from agentic.review.verify import build_request
from agentic.standards.fake import InMemoryStandards, demo_standards
from agentic.standards.repository import PostgresStandards
from agentic.standards.strand_filter import strand_of
from agentic.tuning import load_profile, plan_reviewers
from agentic.wiring import build_chat, build_decision, build_embedder

pytestmark = pytest.mark.live

ESSAY = [
    "School uniforms are a good idea. They make students look the same and that is good.",
    "Some people say uniforms are bad but they are wrong. Uniforms is better because of reasons.",
    "In conclusion uniforms should be required.",
]


def _settings() -> Settings:
    return Settings.load()


def test_live_gemini_full_review():
    s = _settings()
    if not (s.llm_api_key and s.llm_model):
        pytest.skip("LLM_API_KEY / LLM_MODEL not set")
    standards = demo_standards()
    strategy = FullListStrategy(build_chat(s), InMemoryStandards(standards, FakeEmbedder()))
    result = strategy.run(ESSAY, 9)
    allowed = {x.code for x in standards}
    assert {o.standard_code for o in result.observations} <= allowed
    assert result.llm_calls >= 1


def test_live_gemini_embed_dim():
    s = _settings()
    if not (s.embedding_api_key and s.embedding_model):
        pytest.skip("EMBEDDING_API_KEY / EMBEDDING_MODEL not set")
    vectors = build_embedder(s).embed(["x"], "query")
    assert len(vectors) == 1
    assert len(vectors[0]) == s.embedding_dim


def test_live_jev_noul():
    s = _settings()
    if not s.jev_api_key:
        pytest.skip("JEV_API_KEY not set")
    s = s.model_copy(update={"verify": True, "fake_llm": False, "decision_provider": "jev"})
    standards = demo_standards()
    code = standards[2].code
    finding = Finding(standard_code=code, concept=standards[2].concept, description=standards[2].description,
                      paragraphs=[2], reasons=["Claims lack supporting detail"], observation_count=1,
                      summary="Claims are not developed with evidence.")
    met = MetStandard(standard_code=standards[5].code, concept=standards[5].concept)
    state, questions = build_request(ESSAY, 9, [standards[2], standards[5]], [finding], [met])
    result = build_decision(s).noul(state, questions)
    assert result.probabilities
    assert set(result.probabilities) & set(questions)
    assert all(0.0 <= p <= 1.0 for p in result.probabilities.values())


SLICE_COURSE = "2.00-control"
SLICE_SUBMISSIONS = Path(__file__).resolve().parents[2] / "agentic_lab" / "data" / "synthetic" / "courses" / SLICE_COURSE / "submissions"


def test_live_v2_slice_submission():
    """One written 2.00-control submission through review_one with real keys: v2 verdicts and usage for
    every reviewer that has standards. Takes the first essay or paragraph submission; it does not pin the
    module's reading_strand. Skips until the course submissions exist and the v2 catalog is loaded."""
    s = _settings()
    if not (s.llm_api_key and s.llm_model):
        pytest.skip("LLM_API_KEY / LLM_MODEL not set")
    if not SLICE_SUBMISSIONS.is_dir():
        pytest.skip(f"{SLICE_COURSE} submissions not generated")
    try:
        conn = wiring.connect(s)
    except psycopg.Error:
        pytest.skip("Postgres is not reachable")
    with conn:
        if not any(strand_of(x.code) == "R" for x in PostgresStandards(conn).in_range(3, 5)):
            pytest.skip("loaded catalog has no grade 3-5 R standards: load the v2 catalog "
                        "(STANDARDS_FILE=agentic/standards/mls-ela-reviewable-v2.xlsx, then load-standards)")
        s = s.model_copy(update={"lms_source": "coursedir", "data_dir": SLICE_SUBMISSIONS.parents[2],
                                 "fake_llm": False, "verify": False, "review_pipeline": "v2"})
        course_id = f"classroom_{SLICE_COURSE}"
        lms = wiring.build_course_lms(s, course_id)
        written = ("essay", "paragraph")
        sub = next((x for x in lms.list_submissions(course_id)
                    if lms.get_assignment(course_id, x.assignment_id).submission_type in written), None)
        if sub is None:
            pytest.skip("no written submission in the slice")
        result = review_one(s, course_id, sub.assignment_id, sub.user_id, ReviewOptions(), conn=conn)
    assert result.pipeline == "v2"
    grade = lms.get_course(course_id).grade_level
    planned = {spec.id for spec, _, _ in plan_reviewers(load_profile(s.config_dir, s.bands_profile), grade)}
    reviewers = result.trace["reviewers"]
    with_standards = {r for r in planned if reviewers.get(r, {}).get("standards_count", 0) > 0}
    assert with_standards, "no planned reviewer had standards"
    assert with_standards <= {v.reviewer for v in result.verdicts}
    assert all(result.usage[r].input_tokens > 0 for r in with_standards)
