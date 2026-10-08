"""Live smoke tests: call real external APIs. Run with `-m live`; each skips when its key is empty."""
import pytest

from agentic.config import Settings
from agentic.llm.fake import FakeEmbedder
from agentic.models import Finding, MetStandard
from agentic.review.strategies import FullListStrategy
from agentic.review.verify import build_request
from agentic.standards.fake import InMemoryStandards, demo_standards
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
