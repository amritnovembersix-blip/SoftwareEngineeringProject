"""Composition point: the only module that names concrete Adapters."""
import re

from agentic.config import Settings
from agentic.decisions.base import DecisionModel
from agentic.decisions.fake import FakeDecisionModel
from agentic.decisions.jev import JevDecisionModel
from agentic.llm.base import ChatModel, Embedder
from agentic.llm.fake import FakeChatModel, FakeEmbedder
from agentic.llm.gemini import GeminiChatModel, GeminiEmbedder
from agentic.lms.base import LMSClient
from agentic.lms.jsonl_client import JsonlLMSClient
from agentic.results.base import ResultsWriter
from agentic.results.postgres_writer import PostgresResultsWriter
from agentic.review.graph import Reviewer, build_graph
from agentic.review.strategies import (
    AnalysisOut, AssessmentOut, Evidence, FullListStrategy, IssueOut, Judgment, JudgmentOut,
    RagStrategy, StandardAssessment, StandardsSource, StandardsStrategy,
)
from agentic import dataset
from agentic.dataset import load_assignment, normalize_strands
from agentic.standards.fake import InMemoryStandards, demo_standards
from agentic.standards.strand_filter import StrandFilter, TargetFilter

_ASSESS_CODE = re.compile(r"^- (\S+) \|", re.MULTILINE)
_CANDIDATE_CODE = re.compile(r"^\s+- (\S+) \|", re.MULTILINE)
_PARAGRAPH = re.compile(r"^¶(\d+) ", re.MULTILINE)


def demo_fake_chat(extra_handlers: dict | None = None) -> FakeChatModel:
    """Deterministic chat for any essay: standards 1 and 3 not met (paragraph 2), the rest met.
    `extra_handlers` adds schemas other callers need (the lab adds its essay generator's)."""

    def _para(user: str) -> int:
        return min(2, max(1, len(_PARAGRAPH.findall(user))))

    def assess(_system: str, user: str) -> AssessmentOut:
        codes = _ASSESS_CODE.findall(user)
        para = _para(user)
        out = []
        for i, code in enumerate(codes):
            if i in (0, 2):
                reason = "Ideas lack supporting detail" if i == 0 else "Transitions are missing"
                out.append(StandardAssessment(standard_code=code, verdict="weakness", severity="developing",
                                              evidence=[Evidence(paragraph=para, reason=reason)]))
            else:
                out.append(StandardAssessment(standard_code=code, verdict="meets"))
        return AssessmentOut(assessments=out)

    def analyze(_system: str, user: str) -> AnalysisOut:
        p = _para(user)
        return AnalysisOut(issues=[IssueOut(paragraph=p, issue="Rambling structure"),
                                   IssueOut(paragraph=p, issue="Vague wording")])

    def judge(_system: str, user: str) -> JudgmentOut:
        block = next((b for b in user.split("\n\n") if b.startswith("Issue 1 ")), "")
        candidates = _CANDIDATE_CODE.findall(block)
        first = Judgment(issue_id=1, standard_code=candidates[0] if candidates else None,
                         reason="Organization needs clearer focus")
        return JudgmentOut(judgments=[first, Judgment(issue_id=2)])

    return FakeChatModel({AssessmentOut: assess, AnalysisOut: analyze, JudgmentOut: judge,
                          **(extra_handlers or {})})


def _require(s: Settings, *names: str) -> None:
    missing = [n.upper() for n in names if not getattr(s, n)]
    if missing:
        raise ValueError(f"{' and '.join(missing)} required unless FAKE_LLM=1")


def build_chat(s: Settings) -> ChatModel:
    provider = "fake" if s.fake_llm else s.llm_provider
    if provider == "fake":
        return demo_fake_chat()
    if provider == "gemini":
        _require(s, "llm_api_key", "llm_model")
        return GeminiChatModel(s.llm_model, s.llm_api_key, s.llm_rpm)
    raise ValueError(f"unknown LLM_PROVIDER {s.llm_provider!r}")


def build_embedder(s: Settings) -> Embedder:
    provider = "fake" if s.fake_llm else s.embedding_provider
    if provider == "fake":
        return FakeEmbedder(s.embedding_dim)
    if provider == "gemini":
        _require(s, "embedding_api_key", "embedding_model")
        return GeminiEmbedder(s.embedding_model, s.embedding_api_key, s.embedding_dim, s.embedding_rpm)
    raise ValueError(f"unknown EMBEDDING_PROVIDER {s.embedding_provider!r}")


def build_decision(s: Settings) -> DecisionModel | None:
    if not s.verify:
        return None
    provider = "fake" if s.fake_llm else s.decision_provider
    if provider == "fake":
        return FakeDecisionModel(lambda k: 0.8 if k.startswith("finding:") else 0.6)
    if provider == "jev":
        _require(s, "jev_api_key")
        return JevDecisionModel(s.jev_api_key, s.jev_base_url, s.jev_model)
    raise ValueError(f"unknown DECISION_PROVIDER {s.decision_provider!r}")


def build_lms(s: Settings, version: str | None = None) -> LMSClient:
    return JsonlLMSClient(s.data_dir, version or s.classroom)


def connect(s: Settings, autocommit: bool = True):
    import psycopg
    from pgvector.psycopg import register_vector

    conn = psycopg.connect(s.database_url_local, autocommit=autocommit)
    register_vector(conn)
    return conn


def build_results_writer(s: Settings) -> ResultsWriter:
    return PostgresResultsWriter(connect(s))


def build_standards_source(s: Settings, conn=None) -> StandardsSource:
    if s.fake_llm:
        return InMemoryStandards(demo_standards(), FakeEmbedder(s.embedding_dim))
    from agentic.standards.repository import PostgresStandards

    return PostgresStandards(conn or connect(s))


def resolve_strands(s: Settings, version: str | None = None, override=None) -> list[str]:
    """Which strands to review: explicit override > REVIEW_STRANDS > the classroom's assignment file.
    Writing (W) is always included."""
    if override:
        return normalize_strands(override)
    if s.review_strands.strip():
        return normalize_strands(s.review_strands.split(","))
    return load_assignment(s.data_dir, version or s.classroom).strands


def build_review_standards(s: Settings, version: str | None = None, strands=None, conn=None):
    """The standards source a review (or evaluation) uses: the resolved strands, narrowed to the
    assignment's targeted codes when it declares them (Decorators stacked on the source)."""
    scoped = StrandFilter(build_standards_source(s, conn), resolve_strands(s, version, strands))
    targets = load_assignment(s.data_dir, version or s.classroom).targets
    return TargetFilter(scoped, targets) if targets else scoped


def _strategy(s: Settings, name: str | None, chat: ChatModel, embedder: Embedder | None,
              standards: StandardsSource) -> StandardsStrategy:
    name = name or s.review_strategy
    if name == "full":
        return FullListStrategy(chat, standards)
    if name == "rag":
        return RagStrategy(chat, embedder or build_embedder(s), standards, k=s.retrieval_k)
    raise ValueError(f"unknown review strategy {name!r}")


def build_strategy(s: Settings, name: str | None = None) -> StandardsStrategy:
    return _strategy(s, name, build_chat(s), None, build_review_standards(s))


def build_reviewer(s: Settings, strategy: str | None = None, version: str | None = None,
                   strands=None, context: bool | None = None, conn=None) -> Reviewer:
    name = strategy or s.review_strategy
    chat = build_chat(s)
    embedder = build_embedder(s) if name == "rag" else None
    decision = build_decision(s)
    standards = build_review_standards(s, version, strands, conn)
    strat = _strategy(s, name, chat, embedder, standards)
    use_context = s.assignment_context if context is None else context
    graph = build_graph(build_lms(s, version), strat, decision, s.jev_miss_threshold, use_context)
    models = {
        "chat": chat.model_name,
        "embedder": embedder.model_name if embedder else None,
        "decision": (getattr(decision, "model", None) or s.jev_model) if decision else None,
    }
    return Reviewer(graph, strat.name, models=models, strands=standards.strands,
                    targets=getattr(standards, "codes", []), assignment_context=use_context)


def _course_version(course_id: str) -> str | None:
    """With the JSONL LMS a course `classroom_<v>` is classroom version <v>; other IDs -> CLASSROOM."""
    return course_id.removeprefix("classroom_") if course_id.startswith("classroom_") else None


def build_course_reviewer(s: Settings, course_id: str, conn=None) -> Reviewer:
    """Reviewer for an LMS course ID (the HTTP service's entry point)."""
    return build_reviewer(s, version=_course_version(course_id), conn=conn)


def build_course_lms(s: Settings, course_id: str) -> LMSClient:
    return build_lms(s, _course_version(course_id))


def list_course_ids(s: Settings) -> list[str]:
    """Every course the LMS knows about (JSONL LMS: every classroom with an essays file)."""
    return [dataset.course_id(v) for v in dataset.list_versions(s.data_dir)]
