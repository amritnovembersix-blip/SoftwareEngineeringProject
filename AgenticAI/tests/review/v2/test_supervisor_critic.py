"""Review supervisor, Task 3: the critic keeps or drops each growth area against its cited segments."""
from agentic.llm.fake import FakeChatModel
from agentic.models import Observation, StandardRef, Verdict
from agentic.review.v2.supervisor import Critic, CriticCall, CriticOut

SEGMENTS = ["The dogs runs to the park.", "It was sunny, and the birds sang.", "Rainsford felt fear."]
TEMPLATE = "Check.\n$segments\n$growth_areas"


def _ref(code):
    return StandardRef(code=code, concept=".".join(code.split(".")[:4]), description=f"Text of {code}.",
                       grade_min=3, grade_max=3)


def _growth(code, segments=(1,), level="developing"):
    return Verdict(standard_code=code, verdict="growth_area", support_level=level, segments=list(segments),
                   reviewer="3-language", confidence=0.7)


def _obs(code, seg=1):
    return Observation(standard_code=code, paragraph=seg, reason=f"Reason for {code}.", level="sentence")


def _chat(*replies):
    replies = list(replies)
    return FakeChatModel({CriticOut: lambda _s, _u: replies.pop(0) if len(replies) > 1 else replies[0]})


def _run(chat, verdicts, observations=(), max_items=30):
    refs = {v.standard_code: _ref(v.standard_code) for v in verdicts}
    return Critic(chat, TEMPLATE, max_items).run(list(verdicts), list(observations), SEGMENTS, refs)


def _by(verdicts):
    return {v.standard_code: v for v in verdicts}


def test_drop_met_becomes_meets_and_removes_observations():
    vs = [_growth("3.L.1.A.e"), _growth("3.L.1.A.d", [2])]
    reply = CriticOut(calls=[CriticCall(standard="3.L.1.A.e", action="drop", outcome="met", reason="Verbs agree."),
                             CriticCall(standard="3.L.1.A.d", action="keep", reason="Shown.")])
    r = _run(_chat(reply), vs, [_obs("3.L.1.A.e"), _obs("3.L.1.A.d", 2)])
    v = _by(r.verdicts)["3.L.1.A.e"]
    assert (v.verdict, v.support_level, v.segments, v.confidence) == ("meets", None, [], None)
    assert [o.standard_code for o in r.observations] == ["3.L.1.A.d"]
    assert r.changes == [{"standard": "3.L.1.A.e", "step": "critic", "action": "dropped:met",
                          "reason": "Verbs agree.", "segments": [1]}]
    assert _by(r.verdicts)["3.L.1.A.d"] == vs[1] and not r.invalid and not r.capped


def test_drop_not_shown_becomes_not_evident():
    reply = CriticOut(calls=[CriticCall(standard="3.L.1.A.e", action="drop", outcome="not_shown", reason="No.")])
    r = _run(_chat(reply), [_growth("3.L.1.A.e")])
    assert r.verdicts[0].verdict == "not_evident" and r.changes[0]["action"] == "dropped:not_shown"


def test_keep_and_omitted_and_unknown_codes_leave_verdicts_unchanged():
    vs = [_growth("3.L.1.A.e"), _growth("3.L.1.A.d")]
    reply = CriticOut(calls=[CriticCall(standard="3.L.1.A.e", action="keep", reason="Shown."),
                             CriticCall(standard="9.W.9.Z.z", action="drop", outcome="met", reason="?")])
    r = _run(_chat(reply), vs)
    assert r.verdicts == vs and r.changes == []


def test_growth_area_without_segments_is_not_sent():
    chat = _chat(CriticOut(calls=[]))
    r = _run(chat, [_growth("3.L.1.A.e", segments=[])])
    assert chat.calls == [] and r.usage is None and r.verdicts[0].verdict == "growth_area"


def test_no_growth_areas_means_no_call():
    chat = _chat(CriticOut(calls=[]))
    meets = Verdict(standard_code="3.L.1.A.e", verdict="meets", reviewer="3-language")
    r = _run(chat, [meets])
    assert chat.calls == [] and r.verdicts == [meets]


def test_cap_keeps_the_rest_unchecked():
    vs = [_growth(f"3.L.1.A.{i}") for i in range(31)]
    chat = _chat(CriticOut(calls=[CriticCall(standard=v.standard_code, action="drop", outcome="met", reason="x")
                                  for v in vs]))
    r = _run(chat, vs, max_items=30)
    assert r.capped and len(r.changes) == 30
    assert _by(r.verdicts)["3.L.1.A.30"].verdict == "growth_area"  # the 31st was never sent
    assert "3.L.1.A.30 " not in chat.calls[0][2]


def test_a_malformed_item_is_kept_unchecked_without_a_retry():
    bad = CriticOut(calls=[CriticCall(standard="3.L.1.A.e", action="drop", reason="No outcome.")])
    chat = _chat(bad)
    vs = [_growth("3.L.1.A.e")]
    r = _run(chat, vs)
    assert not r.invalid and r.verdicts == vs and r.changes == [] and len(chat.calls) == 1


def test_a_failing_call_twice_keeps_everything_and_is_invalid():
    def boom(_s, _u):
        raise RuntimeError("x")
    chat = FakeChatModel({CriticOut: boom})
    vs = [_growth("3.L.1.A.e")]
    r = _run(chat, vs)
    assert r.invalid and r.verdicts == vs and len(chat.calls) == 2


def test_prompt_shows_standard_text_level_reason_and_numbered_segments():
    chat = _chat(CriticOut(calls=[]))
    _run(chat, [_growth("3.L.1.A.e", [1, 3], level="beginning")], [_obs("3.L.1.A.e")])
    user = chat.calls[0][2]
    for part in ("S1 The dogs runs to the park.", "S3 Rainsford felt fear.", "Text of 3.L.1.A.e.", "beginning",
                 "Reason for 3.L.1.A.e.", "S1, S3"):
        assert part in user, part
