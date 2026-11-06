import pytest

from agentic.config import PACKAGE_ROOT
from agentic.llm.base import DailyQuotaExceeded
from agentic.llm.fake import FakeChatModel
from agentic.models import Finding, StandardRef, Verdict
from agentic.review.v2.root_cause import RootCauseStep, needs_root_cause
from agentic.review.v2.schemas import RootCauseOut, RootDetail, RootLink
from agentic.tuning import load_prompt

TEMPLATE = load_prompt(PACKAGE_ROOT / "prompts", "root_cause.v1")
SEGMENTS = ["First paragraph.", "Second paragraph.", "Third paragraph."]
CONDITIONS = {"L.1.A": ["in longer sentences", "when revising"], "W.2.A": ["with evidence"]}
RATIONALE = "The same sentence control gap shows up in both."


def _f(code, paragraphs):
    return Finding(standard_code=code, concept="c", description="d", paragraphs=paragraphs,
                   reasons=["r"], observation_count=len(paragraphs), summary="s about " + code,
                   support_level="developing")


def _v(code):
    return Verdict(standard_code=code, verdict="growth_area", support_level="developing",
                   segments=[1], reviewer="x")


def _ref(code):
    lo = int(code.split(".")[0].split("-")[0])
    return StandardRef(code=code, concept="c", description="desc " + code, grade_min=lo, grade_max=lo,
                       strand=code.split(".")[1])


ROOT, TOP = "4.L.1.A.a", "7.L.1.A.a"
STANDARDS = {c: _ref(c) for c in (ROOT, TOP, "8.W.2.A.a")}


def _step(reply):
    chat = FakeChatModel({RootCauseOut: lambda s, u: reply(u) if callable(reply) else reply})
    return RootCauseStep(chat, TEMPLATE, CONDITIONS), chat


def _pair(top_paras=(1,), root_paras=(1,)):
    return [_f(TOP, list(top_paras)), _f(ROOT, list(root_paras))]


def test_needs_root_cause_false_without_shared_segment():
    fs = _pair(top_paras=(1,), root_paras=(2,))
    assert not needs_root_cause(fs, [_v(TOP), _v(ROOT)])


def test_needs_root_cause_true_with_shared_segment():
    assert needs_root_cause(_pair(), [_v(TOP), _v(ROOT)])


def test_needs_root_cause_ignores_non_growth_verdicts():
    meets = Verdict(standard_code=ROOT, verdict="meets", reviewer="x")
    assert not needs_root_cause(_pair(), [_v(TOP), meets])


def test_two_high_grades_alone_do_not_qualify():
    assert not needs_root_cause([_f(TOP, [1]), _f("8.W.2.A.a", [1])], [_v(TOP), _v("8.W.2.A.a")])


def test_valid_link_and_details_kept_and_prompt_rendered():
    reply = RootCauseOut(
        links=[RootLink(finding_code=TOP, root_code=ROOT, rationale=RATIONALE)],
        details=[RootDetail(standard_code=TOP, condition="when revising", pattern="consistent",
                            skill_state="missing")])
    step, chat = _step(reply)
    links, details, dropped, usage = step.run(_pair(), SEGMENTS, STANDARDS)
    assert [(l.finding_code, l.root_code) for l in links] == [(TOP, ROOT)]
    assert details[TOP].condition == "when revising" and details[TOP].pattern == "consistent"
    assert dropped == [] and usage.input_tokens > 0
    (_, _, user), = chat.calls
    assert f"- {TOP} | desc {TOP} | s about {TOP}" in user and "[S1] First paragraph." in user
    assert "[S2]" not in user and "in longer sentences" in user and "$" not in user


def test_no_qualifying_findings_makes_no_call():
    step, chat = _step(RootCauseOut(links=[], details=[]))
    links, details, dropped, usage = step.run(_pair(root_paras=(2,)), SEGMENTS, STANDARDS)
    assert chat.calls == [] and (links, details, dropped) == ([], {}, [])
    assert usage.input_tokens == 0 and usage.estimated is False


def test_invented_link_dropped_without_echo():
    reply = RootCauseOut(links=[RootLink(finding_code=TOP, root_code="3.L.9.Z.z", rationale=RATIONALE)],
                         details=[])
    step, _ = _step(reply)
    links, _, dropped, _ = step.run(_pair(), SEGMENTS, STANDARDS)
    assert links == [] and [(d.stage, d.reason) for d in dropped] == [("root_cause", "invented_link")]
    assert "3.L.9.Z.z" not in dropped[0].reason


def test_link_without_shared_segment_dropped():
    third = _f("5.L.1.B.a", [2])
    reply = RootCauseOut(links=[RootLink(finding_code=TOP, root_code="5.L.1.B.a", rationale=RATIONALE)],
                         details=[])
    step, _ = _step(reply)
    # the grade 8 finding on segment 2 makes `third` a participant, but TOP and `third` share no segment
    findings = _pair() + [third, _f("8.W.2.A.a", [2])]
    links, _, dropped, _ = step.run(findings, SEGMENTS, STANDARDS)
    assert links == [] and [d.reason for d in dropped] == ["no_shared_segment"]


def test_two_grade_6_plus_findings_never_link():
    low = "6.L.1.A.a"
    reply = RootCauseOut(links=[RootLink(finding_code=TOP, root_code=low, rationale=RATIONALE),
                                RootLink(finding_code=TOP, root_code=ROOT, rationale=RATIONALE)], details=[])
    step, _ = _step(reply)
    links, _, dropped, _ = step.run(_pair() + [_f(low, [1])], SEGMENTS, {**STANDARDS, low: _ref(low)})
    assert [(l.finding_code, l.root_code) for l in links] == [(TOP, ROOT)]
    assert [d.reason for d in dropped] == ["invented_link"]


def test_two_grade_3_5_findings_never_link():
    reply = RootCauseOut(links=[RootLink(finding_code="5.L.1.A.a", root_code=ROOT, rationale=RATIONALE)],
                         details=[])
    step, _ = _step(reply)
    links, _, dropped, _ = step.run(_pair() + [_f("5.L.1.A.a", [1])], SEGMENTS, STANDARDS)
    assert links == [] and [d.reason for d in dropped] == ["invented_link"]


def test_root_not_below_finding_dropped():
    reply = RootCauseOut(links=[RootLink(finding_code=ROOT, root_code=TOP, rationale=RATIONALE)],
                         details=[])
    step, _ = _step(reply)
    links, _, dropped, _ = step.run(_pair(), SEGMENTS, STANDARDS)
    assert links == [] and [d.reason for d in dropped] == ["invented_link"]


def test_long_rationale_dropped():
    reply = RootCauseOut(links=[RootLink(finding_code=TOP, root_code=ROOT, rationale="word " * 41)],
                         details=[])
    step, _ = _step(reply)
    links, _, dropped, _ = step.run(_pair(), SEGMENTS, STANDARDS)
    assert links == [] and [d.reason for d in dropped] == ["bad_rationale"]


def test_condition_outside_vocabulary_nulled():
    reply = RootCauseOut(links=[], details=[RootDetail(
        standard_code=TOP, condition="made up", pattern="occasional", skill_state="misapplied")])
    step, _ = _step(reply)
    _, details, dropped, _ = step.run(_pair(), SEGMENTS, STANDARDS)
    assert details[TOP].condition is None and details[TOP].pattern == "occasional"
    assert details[TOP].skill_state == "misapplied"
    assert [d.reason for d in dropped] == ["unknown_condition"]


def test_bad_pattern_and_skill_state_nulled():
    reply = RootCauseOut(links=[], details=[RootDetail(
        standard_code=TOP, condition=None, pattern="always", skill_state="lost")])
    step, _ = _step(reply)
    _, details, dropped, _ = step.run(_pair(), SEGMENTS, STANDARDS)
    assert details[TOP].pattern is None and details[TOP].skill_state is None
    assert [d.reason for d in dropped] == ["bad_pattern", "bad_skill_state"]


def test_details_for_non_participating_finding_dropped():
    reply = RootCauseOut(links=[], details=[RootDetail(standard_code="8.W.2.A.a", pattern="consistent")])
    step, _ = _step(reply)
    _, details, dropped, _ = step.run(_pair() + [_f("8.W.2.A.a", [3])], SEGMENTS, STANDARDS)
    assert details == {} and [d.reason for d in dropped] == ["unknown_detail"]


def test_chat_exception_gives_empty_results_and_one_dropped():
    def boom(u):
        raise ValueError("student said secret")
    step, _ = _step(boom)
    links, details, dropped, usage = step.run(_pair(), SEGMENTS, STANDARDS)
    assert (links, details) == ([], {})
    assert [(d.stage, d.reason) for d in dropped] == [("root_cause", "ValueError")]
    assert usage.input_tokens == 0 and usage.estimated is False


def test_daily_quota_propagates():
    def quota(u):
        raise DailyQuotaExceeded("rpd")
    step, _ = _step(quota)
    with pytest.raises(DailyQuotaExceeded):
        step.run(_pair(), SEGMENTS, STANDARDS)


def test_missing_standard_text_skipped_gracefully():
    step, chat = _step(RootCauseOut(links=[], details=[]))
    step.run(_pair(), SEGMENTS, {})
    (_, _, user), = chat.calls
    assert f"- {TOP} | s about {TOP}" in user


def test_duplicate_link_dropped():
    link = RootLink(finding_code=TOP, root_code=ROOT, rationale=RATIONALE)
    step, _ = _step(RootCauseOut(links=[link, link], details=[]))
    links, _, dropped, _ = step.run(_pair(), SEGMENTS, STANDARDS)
    assert len(links) == 1 and [d.reason for d in dropped] == ["duplicate_link"]


def test_prompt_describes_its_reply_fields():
    for field in ("finding_code", "root_code", "rationale", "details", "condition", "pattern",
                  "consistent", "occasional", "skill_state", "missing", "misapplied"):
        assert field in TEMPLATE
    assert "- CODE | standard text | summary" in TEMPLATE
    assert "shared cause in" not in TEMPLATE  # no field holds it; the link rationale explains
