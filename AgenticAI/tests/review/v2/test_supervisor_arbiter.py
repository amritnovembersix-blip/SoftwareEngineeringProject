"""Review supervisor, Task 4: clusters of growth areas on the same segments, and the arbiter (one problem, one
standard). One concept line is never clustered across grades: that is the progression chain."""
from agentic.llm.fake import FakeChatModel
from agentic.models import Observation, StandardRef, Verdict
from agentic.review.v2.supervisor import (
    Arbiter, ArbiterOut, ClusterCall, Problem, clusters, concept_line, grade_of,
)

SEGMENTS = ["The dogs runs to the park.", "It was sunny and the birds sang.", "Rainsford felt fear."]
TEMPLATE = "Arbitrate.\n$segments\n$clusters"


def _growth(code, segments):
    return Verdict(standard_code=code, verdict="growth_area", support_level="developing", segments=list(segments),
                   reviewer="r", confidence=0.6)


def _ref(code):
    return StandardRef(code=code, concept=code, description=f"Text of {code}.", grade_min=3, grade_max=3)


def _chat(*replies):
    replies = list(replies)
    return FakeChatModel({ArbiterOut: lambda _s, _u: replies.pop(0) if len(replies) > 1 else replies[0]})


def _run(chat, verdicts, observations=(), max_clusters=15):
    refs = {v.standard_code: _ref(v.standard_code) for v in verdicts}
    return Arbiter(chat, TEMPLATE, max_clusters).run(list(verdicts), list(observations), SEGMENTS, refs)


def _by(vs):
    return {v.standard_code: v for v in vs}


PAIR = [_growth("6.W.3.A.b", [2]), _growth("6.L.1.A.a", [2])]


def test_concept_line_and_grade():
    assert concept_line("9-10.W.3.A.b") == "W.3.A" and concept_line("3.L.1.A.e") == "L.1.A"
    assert grade_of("9-10.W.3.A.b") == "9-10" and grade_of("3.L.1.A.e") == "3"


def test_clusters_join_overlapping_segments_but_never_one_line_across_grades():
    vs = [_growth("6.W.3.A.b", [2]), _growth("6.L.1.A.a", [2, 3]), _growth("7.W.3.A.b", [2]),
          _growth("3.L.2.A.e", [3]), _growth("5.L.1.B.c", [9])]
    assert clusters(vs) == [["3.L.2.A.e", "6.L.1.A.a", "6.W.3.A.b"]]


def test_clusters_never_join_siblings_on_one_concept_line():
    """Justin, 2026-11-17: the arbiter folded sibling sub-standards (6.W.3.A.b into 6.W.3.A.c) that are separate
    skills; 66 of its 73 planted losses were siblings. They never share a cluster."""
    vs = [_growth("6.W.3.A.b", [2]), _growth("6.W.3.A.c", [2]), _growth("6.L.1.A.a", [2])]
    assert clusters(vs) == [["6.L.1.A.a", "6.W.3.A.b"]]
    assert clusters([_growth("6.W.3.A.b", [2]), _growth("6.W.3.A.c", [2])]) == []


def test_clusters_skip_meets_and_growth_without_segments():
    vs = [_growth("6.W.3.A.b", [2]), _growth("6.W.3.A.a", []),
          Verdict(standard_code="6.W.3.A.c", verdict="meets", segments=[2], reviewer="r")]
    assert clusters(vs) == []


def test_absorbed_members_become_not_evident_and_lose_observations():
    obs = [Observation(standard_code="6.L.1.A.a", paragraph=2, reason="x", level="sentence"),
           Observation(standard_code="6.W.3.A.b", paragraph=2, reason="y", level="sentence")]
    reply = ArbiterOut(clusters=[ClusterCall(cluster=1, problems=[
        Problem(standard="6.W.3.A.b", absorbs=["6.L.1.A.a"], reason="Same run-on sentence.")])])
    r = _run(_chat(reply), PAIR, obs)
    assert _by(r.verdicts)["6.L.1.A.a"].verdict == "not_evident"
    assert _by(r.verdicts)["6.W.3.A.b"] == PAIR[0]
    assert [o.standard_code for o in r.observations] == ["6.W.3.A.b"]
    assert r.changes == [{"standard": "6.L.1.A.a", "step": "arbiter", "action": "absorbed", "into": "6.W.3.A.b",
                          "reason": "Same run-on sentence.", "segments": [2]}]


def test_two_problems_in_one_cluster_both_stay():
    reply = ArbiterOut(clusters=[ClusterCall(cluster=1, problems=[
        Problem(standard="6.W.3.A.b", reason="One."), Problem(standard="6.L.1.A.a", reason="Two.")])])
    r = _run(_chat(reply), PAIR)
    assert r.verdicts == PAIR and r.changes == [] and not r.invalid


def test_every_member_must_be_chosen_or_absorbed_else_retry_then_unchanged():
    missing = ArbiterOut(clusters=[ClusterCall(cluster=1, problems=[Problem(standard="6.W.3.A.b", reason="x")])])
    chat = _chat(missing)
    r = _run(chat, PAIR)
    assert r.invalid and r.verdicts == PAIR and len(chat.calls) == 2


def test_a_retry_that_is_valid_is_used():
    missing = ArbiterOut(clusters=[ClusterCall(cluster=1, problems=[Problem(standard="6.W.3.A.b", reason="x")])])
    good = ArbiterOut(clusters=[ClusterCall(cluster=1, problems=[
        Problem(standard="6.W.3.A.b", absorbs=["6.L.1.A.a"], reason="Same.")])])
    r = _run(_chat(missing, good), PAIR)
    assert not r.invalid and _by(r.verdicts)["6.L.1.A.a"].verdict == "not_evident"


def test_chosen_standard_outside_the_cluster_is_invalid():
    reply = ArbiterOut(clusters=[ClusterCall(cluster=1, problems=[
        Problem(standard="3.L.1.A.e", absorbs=["6.L.1.A.a", "6.W.3.A.b"], reason="x")])])
    assert _run(_chat(reply), PAIR).invalid


def test_member_in_two_problems_is_invalid():
    reply = ArbiterOut(clusters=[ClusterCall(cluster=1, problems=[
        Problem(standard="6.W.3.A.b", absorbs=["6.L.1.A.a"], reason="x"),
        Problem(standard="6.L.1.A.a", reason="y")])])
    assert _run(_chat(reply), PAIR).invalid


def test_no_cluster_of_two_means_no_call():
    chat = _chat(ArbiterOut(clusters=[]))
    r = _run(chat, [_growth("6.W.3.A.b", [2]), _growth("7.W.3.A.b", [2])])  # one line across grades
    assert chat.calls == [] and r.usage is None


def test_cap_leaves_extra_clusters_unchanged():
    vs = [g for i in range(1, 4) for g in (_growth(f"{i + 2}.L.1.A.a", [i]), _growth(f"{i + 2}.L.2.B.a", [i]))]
    chat = _chat(ArbiterOut(clusters=[ClusterCall(cluster=n, problems=[
        Problem(standard=f"{n + 2}.L.1.A.a", absorbs=[f"{n + 2}.L.2.B.a"], reason="Same.")]) for n in (1, 2)]))
    r = _run(chat, vs, max_clusters=2)
    assert r.capped and len(r.changes) == 2
    assert _by(r.verdicts)["5.L.2.B.a"].verdict == "growth_area"  # the third cluster was never sent
    assert "5.L.2.B.a" not in chat.calls[0][2]
