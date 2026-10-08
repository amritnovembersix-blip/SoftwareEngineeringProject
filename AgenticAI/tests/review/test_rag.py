import json

from agentic.llm.fake import FakeChatModel, FakeEmbedder
from agentic.review.strategies import (
    AnalysisOut, IssueOut, Judgment, JudgmentOut, RagStrategy,
)
from agentic.standards.fake import InMemoryStandards, demo_standards

PARAS = ["one", "two", "three"]
ISSUE1 = "Sentences ramble without clear structure here"
ISSUE2 = "Evidence is thin and unsupported overall"


class CountingEmbedder(FakeEmbedder):
    def __init__(self):
        super().__init__()
        self.query_calls = []

    def embed(self, texts, kind):
        if kind == "query":
            self.query_calls.append(list(texts))
        return super().embed(texts, kind)


def _build(issues, judge, k=3, max_issues=8):
    emb = CountingEmbedder()
    store = InMemoryStandards(demo_standards(), emb)
    chat = FakeChatModel({AnalysisOut: lambda s, u: AnalysisOut(issues=issues),
                          JudgmentOut: lambda s, u: JudgmentOut(judgments=judge)})
    return RagStrategy(chat, emb, store, k=k, max_issues=max_issues), chat, emb, store


def test_rag_flow_calls_and_trace():
    issues = [IssueOut(paragraph=1, issue=ISSUE1), IssueOut(paragraph=2, issue=ISSUE2)]
    strat, chat, emb, store = _build(issues, [])
    # judge picks a real candidate for issue 1: discover candidates via the trace after a first run
    strat_probe = strat.run(PARAS, 9)
    cand = strat_probe.trace["candidates"][1][0]
    chat._handlers[JudgmentOut] = lambda s, u: JudgmentOut(judgments=[
        Judgment(issue_id=1, standard_code=cand, reason="Weak organization"),
        Judgment(issue_id=2, standard_code=None, reason=None)])
    chat.calls.clear(); emb.query_calls.clear()
    res = strat.run(PARAS, 9)
    assert [(o.standard_code, o.paragraph, o.reason) for o in res.observations] == [(cand, 1, "Weak organization")]
    assert res.llm_calls == 2 and len(chat.calls) == 2
    assert emb.query_calls == [[ISSUE1, ISSUE2]]
    assert set(res.trace["candidates"]) == {1, 2} and res.trace["issue_count"] == 2
    assert all(len(v) == 3 for v in res.trace["candidates"].values())
    dumped = json.dumps(res.trace)
    assert ISSUE1 not in dumped and ISSUE2 not in dumped
    assert res.dropped == []
    assert res.standards == store.at_grade(9)
    assert strat.name == "rag"


def test_rag_guards():
    issues = [IssueOut(paragraph=1, issue=ISSUE1), IssueOut(paragraph=9, issue=ISSUE2),
              IssueOut(paragraph=2, issue="third"), IssueOut(paragraph=3, issue="fourth")]
    strat, chat, emb, store = _build(issues, [])
    probe = strat.run(PARAS, 9)
    cands = probe.trace["candidates"]
    outside = next(s.code for s in store.at_grade(9) if s.code not in cands[1])
    thirteen = " ".join(["w"] * 13)
    chat._handlers[JudgmentOut] = lambda s, u: JudgmentOut(judgments=[
        Judgment(issue_id=1, standard_code=outside, reason="leaky essay quote"),
        Judgment(issue_id=5, standard_code=cands[1][0], reason="x"),
        Judgment(issue_id=2, standard_code=cands[2][0], reason="Weak"),
        Judgment(issue_id=3, standard_code=cands[3][0], reason=thirteen),
        Judgment(issue_id=4, standard_code=cands[4][0], reason=" ")])
    res = strat.run(PARAS, 9)
    assert res.observations == []
    r = [d.reason for d in res.dropped]
    assert all(d.stage == "judge" for d in res.dropped)
    assert r == [f"not_a_candidate:{outside}", "unknown_issue:5", "bad_paragraph:9",
                 f"reason_too_long:{cands[3][0]}", f"reason_blank:{cands[4][0]}"]
    assert not any("leaky" in x for x in r)


def test_rag_truncates_issues():
    issues = [IssueOut(paragraph=1, issue=f"issue number {i}") for i in range(10)]
    strat, chat, emb, store = _build(issues, [])
    res = strat.run(PARAS, 9)
    assert res.trace["issue_count"] == 8 and len(emb.query_calls[0]) == 8
    assert [d.reason for d in res.dropped] == ["too_many_issues"]


def test_rag_no_issues_skips_judge():
    strat, chat, emb, store = _build([], [])
    res = strat.run(PARAS, 9)
    assert res.llm_calls == 1 and res.observations == [] and len(chat.calls) == 1
    assert emb.query_calls == []
    assert res.trace == {"candidates": {}, "issue_count": 0}


def test_rag_unknown_code_is_not_echoed():
    strat, chat, emb, store = _build([IssueOut(paragraph=1, issue=ISSUE1)], [])
    chat._handlers[JudgmentOut] = lambda s, u: JudgmentOut(judgments=[
        Judgment(issue_id=1, standard_code="SECRET ESSAY text", reason="Weak")])
    res = strat.run(PARAS, 9)
    assert res.observations == []
    assert [d.reason for d in res.dropped] == ["unknown_standard"]


def test_rag_duplicate_judgments_use_first():
    strat, chat, emb, store = _build([IssueOut(paragraph=1, issue=ISSUE1)], [])
    cand = strat.run(PARAS, 9).trace["candidates"][1]
    chat._handlers[JudgmentOut] = lambda s, u: JudgmentOut(judgments=[
        Judgment(issue_id=1, standard_code=cand[0], reason="First"),
        Judgment(issue_id=1, standard_code=cand[1], reason="Second")])
    res = strat.run(PARAS, 9)
    assert [(o.standard_code, o.reason) for o in res.observations] == [(cand[0], "First")]
    assert [d.reason for d in res.dropped] == ["duplicate_judgment:1"]
