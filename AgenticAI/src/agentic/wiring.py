"""Composition point: the only module that names concrete Adapters."""
import re
from pathlib import Path
from functools import lru_cache

from agentic.analytics.base import AnalyticsWriter, ResultsReader
from agentic.analytics.memory import InMemoryAnalyticsWriter
from agentic.analytics.postgres import PostgresAnalyticsWriter, PostgresResultsReader
from agentic.config import Settings
from agentic.decisions.base import DecisionModel
from agentic.decisions.fake import FakeDecisionModel
from agentic.decisions.jev import JevDecisionModel
from agentic.llm.base import ChatModel, Embedder, chat_key, model_label
from agentic.llm.fake import FakeChatModel, FakeEmbedder
from agentic.llm.gemini import GeminiChatModel, GeminiEmbedder
from agentic.llm.ratelimit import RateLimiter
from agentic.lms.base import LMSClient
from agentic.lms.coursedir_client import CourseDirLMSClient
from agentic.lms.jsonl_client import JsonlLMSClient
from agentic.results.base import ResultsWriter
from agentic.results.postgres_writer import PostgresResultsWriter
from agentic.review.graph import Reviewer, build_graph
from agentic.review.v2.graph import ROOT_CAUSE_PROMPT, ReviewerV2, build_v2_graph, effective_profile
from agentic.review.v2.options import ResolvedOptions, ReviewOptions
from agentic.review.v2.schemas import (
    PerStandardOut, ProgressionJudgment, ProgressionOut, RootCauseOut, SegmentEvidence, StandardJudgment,
    StepJudgment, StepLevel, StepOut,
)
from agentic.review.strategies import (
    AnalysisOut, AssessmentOut, Evidence, FullListStrategy, IssueOut, Judgment, JudgmentOut,
    RagStrategy, StandardAssessment, StandardsSource, StandardsStrategy,
)
from agentic import dataset
from agentic.dataset import load_assignment, normalize_strands
from agentic.settings.base import InMemorySettingsStore, SettingsStore, effective, load_defs
from agentic.standards.fake import InMemoryStandards, demo_standards, in_memory_v2_standards
from agentic.standards.strand_filter import StrandFilter, TargetFilter
from agentic.tuning import load_conditions, load_profile, load_prompt, tuning_hash

_ASSESS_CODE = re.compile(r"^- (\S+) \|", re.MULTILINE)
_CANDIDATE_CODE = re.compile(r"^\s+- (\S+) \|", re.MULTILINE)
_PARAGRAPH = re.compile(r"^¶(\d+) ", re.MULTILINE)
_GROUP = re.compile(r"^## ", re.MULTILINE)
_STEP_GRADE = re.compile(r"^- \S+ \(grade (\d+)\) \|", re.MULTILINE)


def demo_fake_chat(extra_handlers: dict | None = None) -> FakeChatModel:
    """Deterministic chat for any essay: standards 1 and 3 not met (paragraph 2), the rest met.
    v2: per standard, the first listed code is a growth area (developing, citing S1) and the rest meet;
    by progression, each group's working grade is its lowest step; the search (one step per group) gives
    the same verdicts: grade 6 meets, grade 7 is approaching, higher grades developing; root cause links nothing.
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

    s1 = [SegmentEvidence(segment=1, reason="Ideas lack supporting detail")]

    def per_standard(_system: str, user: str) -> PerStandardOut:
        codes = _ASSESS_CODE.findall(user)
        return PerStandardOut(judgments=[
            StandardJudgment(standard_code=c, verdict="growth_area", support_level="developing", evidence=s1)
            if i == 0 else StandardJudgment(standard_code=c, verdict="meets") for i, c in enumerate(codes)])

    def progression(_system: str, user: str) -> ProgressionOut:
        out = []
        for block in _GROUP.split(user)[1:]:
            grades = [int(g) for g in _STEP_GRADE.findall(block)]
            if not grades:
                continue
            growth = sorted(set(grades))[1:]  # steps above the lowest are growth areas
            # approaching for the first unmet step, developing for the rest
            steps = [StepLevel(grade=g, support_level="approaching" if i == 0 else "developing")
                     for i, g in enumerate(growth)]
            out.append(ProgressionJudgment(group=block.split(None, 1)[0], working_grade=min(grades),
                                           step_levels=steps, evidence=s1 if growth else []))
        return ProgressionOut(progressions=out)

    def search(_system: str, user: str) -> StepOut:
        out = []
        for block in _GROUP.split(user)[1:]:
            grades = _STEP_GRADE.findall(block)
            if not grades:
                continue
            grade, group = int(grades[0]), block.split(None, 1)[0]
            out.append(StepJudgment(group=group, verdict="meets") if grade <= 6 else StepJudgment(
                group=group, verdict="growth_area", support_level="approaching" if grade == 7 else "developing",
                evidence=s1))
        return StepOut(judgments=out)

    def root_cause(_system: str, _user: str) -> RootCauseOut:
        return RootCauseOut(links=[], details=[])

    return FakeChatModel({AssessmentOut: assess, AnalysisOut: analyze, JudgmentOut: judge,
                          PerStandardOut: per_standard, ProgressionOut: progression, StepOut: search,
                          RootCauseOut: root_cause,
                          **(extra_handlers or {})})


def _require(s: Settings, *names: str) -> None:
    missing = [n.upper() for n in names if not getattr(s, n)]
    if missing:
        raise ValueError(f"{' and '.join(missing)} required unless FAKE_LLM=1")


@lru_cache(maxsize=None)
def _shared_limiter(provider: str, model: str, rpm: int,
                    tpm: int | None = None, rpd: int | None = None) -> RateLimiter:
    """One calls-per-minute, tokens-per-minute and calls-per-day budget per provider and model for the
    whole process, however many chat clients parallel reviews create."""
    return RateLimiter(rpm, tpm=tpm, rpd=rpd)


def build_chat(s: Settings) -> ChatModel:
    provider = "fake" if s.fake_llm else s.llm_provider
    if provider == "fake":
        return demo_fake_chat()
    if provider == "gemini":
        _require(s, "llm_api_key", "llm_model")
        return GeminiChatModel(s.llm_model, s.llm_api_key, s.llm_rpm, reasoning=s.llm_reasoning, timeout_s=s.llm_timeout_s,
                               limiter=_shared_limiter("gemini", s.llm_model, s.llm_rpm, s.llm_tpm, s.llm_rpd))
    raise ValueError(f"unknown LLM_PROVIDER {s.llm_provider!r}")


def build_embedder(s: Settings) -> Embedder:
    provider = "fake" if s.fake_llm else s.embedding_provider
    if provider == "fake":
        return FakeEmbedder(s.embedding_dim)
    if provider == "gemini":
        _require(s, "embedding_api_key", "embedding_model")
        return GeminiEmbedder(s.embedding_model, s.embedding_api_key, s.embedding_dim, s.embedding_rpm,
                              timeout_s=s.llm_timeout_s)
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
    if s.lms_source == "coursedir":
        return CourseDirLMSClient(s.data_dir, version or s.classroom)
    return JsonlLMSClient(s.data_dir, version or s.classroom)  # lms_source is validated as jsonl or coursedir


def connect(s: Settings, autocommit: bool = True):
    import psycopg
    from pgvector.psycopg import register_vector

    conn = psycopg.connect(s.database_url_local, autocommit=autocommit, connect_timeout=10,
                           options=f"-c statement_timeout={s.db_statement_timeout_s * 1000}")
    register_vector(conn)
    return conn


def build_results_writer(s: Settings) -> ResultsWriter:
    return PostgresResultsWriter(connect(s))


def build_analytics_reader(s: Settings) -> ResultsReader:
    return PostgresResultsReader(connect(s))


def build_analytics_writer(s: Settings) -> AnalyticsWriter:
    return PostgresAnalyticsWriter(connect(s))


def build_memory_analytics_writer() -> AnalyticsWriter:
    """A writer that keeps nothing past the run (`analyze --no-save`)."""
    return InMemoryAnalyticsWriter()


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


@lru_cache(maxsize=4)
def _v2_catalog(path: Path) -> InMemoryStandards:
    return in_memory_v2_standards(path)  # read once per process: every fake reviewer shares it


def build_v2_standards(s: Settings, conn=None) -> StandardsSource:
    """The whole catalog in STANDARDS_FILE (fake) or Postgres; the band reviewers scope it."""
    if s.fake_llm:
        return _v2_catalog(s.standards_file)
    from agentic.standards.repository import PostgresStandards

    return PostgresStandards(conn or connect(s))


def build_settings_store(s: Settings, conn=None) -> SettingsStore:
    """Teacher/admin setting overrides: in memory under FAKE_LLM=1 (the caller keeps the store alive),
    else Postgres on `conn` (a new connection when None; the store's close() closes it)."""
    defs = load_defs(s.config_dir)
    if s.fake_llm:
        return InMemorySettingsStore(defs)
    from agentic.settings.postgres_store import PostgresSettingsStore

    return PostgresSettingsStore(conn or connect(s), defs)


def resolve_options(s: Settings, options: ReviewOptions | None = None,
                    store: SettingsStore | None = None) -> ResolvedOptions:
    """Options against the effective teacher/admin settings: the TOML defaults plus the store's
    overrides (none without a store), snapshotted now."""
    rows = store.overrides() if store is not None else []
    return (options or ReviewOptions()).resolve(s, effective(load_defs(s.config_dir), rows))


def build_v2_chats(s: Settings, profile) -> dict[str, ChatModel]:
    """One chat per distinct (reviewer `model`, reasoning level), keyed by `chat_key`; rate limits are
    shared per provider and model, whatever the level. A reviewer on the default model at the global
    level uses the default chat, so it has no entry. Under FAKE_LLM every reviewer uses the default fake chat."""
    if s.fake_llm:
        return {}
    chats: dict[str, ChatModel] = {}
    for r in profile.reviewers:
        level = r.reasoning or "default"
        if not r.model and level == s.llm_reasoning:
            continue
        key = chat_key(r.model, level)
        if key not in chats:
            chats[key] = build_chat(s.model_copy(update={"llm_model": r.model or s.llm_model,
                                                         "llm_reasoning": level}))
    return chats


def build_v2_reviewer(s: Settings, version: str | None = None, options: ResolvedOptions | None = None,
                      conn=None) -> ReviewerV2:
    options = options or resolve_options(s)
    profile = effective_profile(load_profile(s.config_dir, options.profile), options)
    conditions = load_conditions(s.config_dir)
    refs = {r.prompt for r in profile.reviewers} | ({ROOT_CAUSE_PROMPT} if options.root_cause else set())
    templates = {ref: load_prompt(s.prompts_dir, ref) for ref in sorted(refs)}
    chat = build_chat(s)
    decision = build_decision(s.model_copy(update={"verify": options.verify}))
    graph = build_v2_graph(build_lms(s, version), build_v2_standards(s, conn), chat, decision, conditions,
                           templates, options, profile, s.jev_miss_threshold,
                           chats=build_v2_chats(s, profile))
    models = {
        "chat": model_label(chat),
        "embedder": None,
        "decision": (getattr(decision, "model", None) or s.jev_model) if decision else None,
    }
    strands = list(dict.fromkeys(x for r in profile.reviewers for x in r.strands))
    return ReviewerV2(graph, options, tuning_hash(profile, conditions, templates), models=models,
                      strands=strands)


def build_reviewer(s: Settings, strategy: str | None = None, version: str | None = None,
                   strands=None, context: bool | None = None, conn=None,
                   options: ResolvedOptions | None = None) -> Reviewer | ReviewerV2:
    """REVIEW_PIPELINE picks the graph. strategy, strands and context apply to the MVP pipeline only."""
    if s.review_pipeline == "v2":
        return build_v2_reviewer(s, version, options, conn)
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


def build_course_reviewer(s: Settings, course_id: str, conn=None,
                          options: ResolvedOptions | None = None) -> Reviewer | ReviewerV2:
    """Reviewer for an LMS course ID (the HTTP service's entry point)."""
    return build_reviewer(s, version=_course_version(course_id), conn=conn, options=options)


def build_course_lms(s: Settings, course_id: str) -> LMSClient:
    return build_lms(s, _course_version(course_id))


def list_course_ids(s: Settings) -> list[str]:
    """Every course the LMS knows about (JSONL LMS: every classroom with an essays file; course
    folders: every folder under courses/ that has a course.json)."""
    if s.lms_source == "coursedir":
        root = Path(s.data_dir) / "courses"
        if not root.is_dir():
            return []
        return [f"classroom_{d.name}" for d in sorted(root.iterdir()) if (d / "course.json").is_file()]
    return [dataset.course_id(v) for v in dataset.list_versions(s.data_dir)]
