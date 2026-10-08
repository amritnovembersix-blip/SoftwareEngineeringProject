from agentic.llm.fake import FakeChatModel
from agentic.models import StandardRef
from agentic.review.strategies import (
    AssessmentOut, Evidence, FullListStrategy, StandardAssessment,
)


def _std(code):
    return StandardRef(code=code, concept=f"Concept {code}", description=f"Desc {code}",
                       grade_min=9, grade_max=10)


class Source:
    def __init__(self, rows):
        self.rows = rows

    def at_grade(self, grade):
        return list(self.rows)

    def search(self, query_embedding, grade, k):
        raise AssertionError("not used")


STANDARDS = [_std("A"), _std("B"), _std("C")]
PARAS = ["one", "two", "three"]


def _strategy(out):
    chat = FakeChatModel({AssessmentOut: lambda s, u: out})
    return FullListStrategy(chat, Source(STANDARDS)), chat


def _met(code):
    return StandardAssessment(standard_code=code, verdict="meets", evidence=[])


def test_full_list_weakness_becomes_observations():
    out = AssessmentOut(assessments=[
        StandardAssessment(standard_code="A", verdict="weakness", severity="developing", evidence=[
            Evidence(paragraph=1, reason="Weak focus"), Evidence(paragraph=3, reason="No conclusion")]),
        _met("B"), _met("C")])
    strat, chat = _strategy(out)
    res = strat.run(PARAS, 9)
    assert [(o.standard_code, o.paragraph) for o in res.observations] == [("A", 1), ("A", 3)]
    assert res.llm_calls == 1 and res.dropped == []
    assert res.standards == STANDARDS
    user = chat.calls[0][2]
    for s in STANDARDS:
        assert s.code in user and s.concept in user and s.description in user
    assert strat.name == "full"


def test_full_list_guards():
    thirteen = " ".join(["w"] * 13)
    out = AssessmentOut(assessments=[
        StandardAssessment(standard_code="ZZ", verdict="weakness", severity="developing", evidence=[Evidence(paragraph=1, reason="x")]),
        StandardAssessment(standard_code="A", verdict="weakness", severity="developing", evidence=[
            Evidence(paragraph=7, reason="secret essay words")]),
        StandardAssessment(standard_code="B", verdict="weakness", severity="developing", evidence=[
            Evidence(paragraph=1, reason=thirteen), Evidence(paragraph=2, reason="   ")]),
        _met("C")])
    res = _strategy(out)[0].run(PARAS, 9)
    assert res.observations == []
    reasons = [d.reason for d in res.dropped]
    assert all(d.stage == "assess" for d in res.dropped)
    assert reasons == ["unknown_standard", "bad_paragraph:7", "no_valid_evidence:A",
                       "reason_too_long:B", "reason_blank:B", "no_valid_evidence:B"]
    assert not any("secret" in r or "w w" in r for r in reasons)


def test_full_list_omitted_and_duplicate_standards():
    out = AssessmentOut(assessments=[
        StandardAssessment(standard_code="A", verdict="weakness", severity="developing", evidence=[Evidence(paragraph=1, reason="Weak")]),
        StandardAssessment(standard_code="A", verdict="weakness", severity="developing", evidence=[Evidence(paragraph=2, reason="Weak")]),
        _met("B")])
    res = _strategy(out)[0].run(PARAS, 9)
    assert [(o.standard_code, o.paragraph) for o in res.observations] == [("A", 1)]
    assert [d.reason for d in res.dropped] == ["duplicate_assessment:A", "not_assessed:C"]


def test_full_list_all_met_gives_no_observations():
    out = AssessmentOut(assessments=[_met("A"), _met("B"), _met("C")])
    res = _strategy(out)[0].run(PARAS, 9)
    assert res.observations == [] and res.dropped == []


def test_full_list_unknown_code_is_not_echoed():
    out = AssessmentOut(assessments=[
        StandardAssessment(standard_code="SECRET ESSAY text", verdict="meets", evidence=[]),
        _met("A"), _met("B"), _met("C")])
    res = _strategy(out)[0].run(PARAS, 9)
    assert [d.reason for d in res.dropped] == ["unknown_standard"]


# --- verdicts: meets | weakness | not_evident -------------------------------------------------

def _run_with(assessments):
    strategy, _ = _strategy(AssessmentOut(assessments=assessments))
    return strategy.run(PARAS, 9)


def test_not_evident_is_neither_finding_nor_met():
    res = _run_with([
        StandardAssessment(standard_code="A", verdict="not_evident", evidence=[Evidence(paragraph=1, reason="x")]),
        StandardAssessment(standard_code="B", verdict="meets"),
        StandardAssessment(standard_code="C", verdict="weakness", severity="beginning",
                           evidence=[Evidence(paragraph=2, reason="No evidence cited")]),
    ])
    assert res.not_evident == ["A"]
    assert res.met_codes == ["B"]
    assert [(o.standard_code, o.severity) for o in res.observations] == [("C", "beginning")]


def test_weakness_without_valid_severity_is_dropped():
    res = _run_with([
        StandardAssessment(standard_code="A", verdict="weakness", severity=None,
                           evidence=[Evidence(paragraph=1, reason="Weak")]),
        StandardAssessment(standard_code="B", verdict="weakness", severity="severe",
                           evidence=[Evidence(paragraph=1, reason="Weak")]),
        StandardAssessment(standard_code="C", verdict="meets"),
    ])
    assert res.observations == []
    assert [d.reason for d in res.dropped] == ["bad_severity:A", "bad_severity:B"]


def test_unknown_verdict_is_dropped():
    res = _run_with([StandardAssessment(standard_code="A", verdict="maybe"),
                     StandardAssessment(standard_code="B", verdict="meets"),
                     StandardAssessment(standard_code="C", verdict="meets")])
    assert "bad_verdict:A" in [d.reason for d in res.dropped]
    assert res.met_codes == ["B", "C"]
