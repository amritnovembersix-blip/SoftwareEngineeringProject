import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic.decisions.fake import FakeDecisionModel
from agentic.llm.fake import FakeChatModel, FakeEmbedder
from agentic.lms.jsonl_client import JsonlLMSClient
from agentic.models import SubmissionRef
from agentic.review.graph import Reviewer, build_graph
from agentic.review.strategies import (
    AnalysisOut, AssessmentOut, Evidence, FullListStrategy, IssueOut, JudgmentOut,
    RagStrategy, StandardAssessment,
)
from agentic.standards.fake import InMemoryStandards, demo_standards

NODES = ["fetch", "number_paragraphs", "select_and_assess", "aggregate", "verify"]
PARAS = ["Secret essay sentence alpha.", "Secret essay sentence beta."]


@pytest.fixture
def ref(tmp_path):
    write_labels(tmp_path, "1.01", [label_row(grade=9)])
    write_essays(tmp_path, "1.01", [essay_row(paragraphs=PARAS, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1")])
    return tmp_path, SubmissionRef(course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1",
                                   user_id="a" * 64, submission_id="sub-1", attempt=1)


def _full(tmp_path):
    store = InMemoryStandards(demo_standards(), FakeEmbedder())
    first = store.at_grade(9)[0].code
    chat = FakeChatModel({AssessmentOut: lambda s, u: AssessmentOut(assessments=[
        StandardAssessment(standard_code=first, verdict="weakness", severity="developing",
                           evidence=[Evidence(paragraph=1, reason="Weak focus")])])})
    return FullListStrategy(chat, store), chat


def _reviewer(tmp_path, strategy):
    graph = build_graph(JsonlLMSClient(tmp_path, "1.01"), strategy, FakeDecisionModel(lambda k: 0.9), 0.5)
    return Reviewer(graph, strategy.name)


def test_graph_end_to_end_full(ref):
    tmp_path, r = ref
    strat, _ = _full(tmp_path)
    res = _reviewer(tmp_path, strat).run(r)
    assert res.strategy == "full" and res.course_grade == 9
    assert res.findings and all(f.p_correct is not None for f in res.findings)
    assert res.verification.status == "ok"
    assert set(res.timings) == set(NODES)
    assert res.submission_ref.submitted_at is not None


def test_graph_end_to_end_rag(ref):
    tmp_path, r = ref
    emb = FakeEmbedder()
    store = InMemoryStandards(demo_standards(), emb)
    chat = FakeChatModel({
        AnalysisOut: lambda s, u: AnalysisOut(issues=[IssueOut(paragraph=1, issue="Rambling structure")]),
        JudgmentOut: lambda s, u: JudgmentOut(judgments=[]),
    })
    res = _reviewer(tmp_path, RagStrategy(chat, emb, store, k=3, max_issues=8)).run(r)
    assert res.strategy == "rag" and res.llm_calls == 2
    assert res.trace["candidates"]


def test_stream_yields_nodes_in_order(ref):
    tmp_path, r = ref
    strat, _ = _full(tmp_path)
    reviewer = _reviewer(tmp_path, strat)
    out = list(reviewer.stream(r))
    assert [n for n, _ in out] == NODES
    assert all(isinstance(s, float) and s >= 0 for _, s in out)
    assert reviewer.last_result is not None and reviewer.last_result.strategy == "full"


def test_result_contains_no_essay_text(ref):
    tmp_path, r = ref
    strat, _ = _full(tmp_path)
    dumped = _reviewer(tmp_path, strat).run(r).model_dump_json()
    for p in PARAS:
        assert p not in dumped


def test_rag_result_contains_no_essay_text(ref):
    tmp_path, r = ref
    emb = FakeEmbedder()
    store = InMemoryStandards(demo_standards(), emb)
    chat = FakeChatModel({
        AnalysisOut: lambda s, u: AnalysisOut(issues=[IssueOut(paragraph=1, issue="Rambling structure")]),
        JudgmentOut: lambda s, u: JudgmentOut(judgments=[]),
    })
    dumped = _reviewer(tmp_path, RagStrategy(chat, emb, store, k=3, max_issues=8)).run(r).model_dump_json()
    for p in PARAS:
        assert p not in dumped
    assert "<p>" not in dumped


def test_strategy_dropped_reaches_result(ref):
    tmp_path, r = ref
    store = InMemoryStandards(demo_standards(), FakeEmbedder())
    chat = FakeChatModel({AssessmentOut: lambda s, u: AssessmentOut(assessments=[
        StandardAssessment(standard_code="NOPE", verdict="weakness", severity="developing",
                           evidence=[Evidence(paragraph=1, reason="Weak focus")])])})
    res = _reviewer(tmp_path, FullListStrategy(chat, store)).run(r)
    assert res.dropped


def test_not_evident_reaches_result_and_is_not_verified(ref):
    tmp_path, r = ref
    store = InMemoryStandards(demo_standards(), FakeEmbedder())
    codes = [x.code for x in store.at_grade(9)]
    chat = FakeChatModel({AssessmentOut: lambda s, u: AssessmentOut(assessments=[
        StandardAssessment(standard_code=codes[0], verdict="not_evident"),
        *[StandardAssessment(standard_code=c, verdict="meets") for c in codes[1:]]])})
    res = _reviewer(tmp_path, FullListStrategy(chat, store)).run(r)
    assert res.not_evident == [codes[0]]
    assert codes[0] not in [m.standard_code for m in res.met_standards]
    assert res.findings == []


def test_fetch_asks_lms_for_assignment_only_when_context_on():
    from agentic.lms.base import Assignment, Course, Submission
    from agentic.models import SubmissionRef
    from agentic.review.nodes import make_fetch

    calls = []

    class Lms:
        def get_course(self, c): return Course(id=c, name="x", subject="ela", grade_level=9)
        def get_submission(self, c, a, u):
            from datetime import datetime, timezone
            return Submission(id="s", user_id=u, assignment_id=a, attempt=1, body="<p>x</p>",
                              submitted_at=datetime.now(timezone.utc))
        def get_assignment(self, c, a):
            calls.append(a)
            return Assignment(id=a, name="T", description="P")

    ref = SubmissionRef(course_id="c", assignment_id="a", user_id="u", submission_id="s", attempt=1)
    assert make_fetch(Lms(), use_context=False)({"submission_ref": ref}).get("assignment") is None
    assert calls == []
    out = make_fetch(Lms(), use_context=True)({"submission_ref": ref})
    assert out["assignment"].description == "P" and calls == ["a"]
