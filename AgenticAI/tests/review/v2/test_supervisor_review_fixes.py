"""Review supervisor: fixes from the whole-branch review (findings 1-5)."""
from pathlib import Path

import pytest

from agentic.config import PACKAGE_ROOT
from agentic.coursedata import lms_assignment_id
from agentic.llm.fake import FakeChatModel
from agentic.lms.base import Assignment
from agentic.lms.coursedir_client import CourseDirLMSClient
from agentic.models import StandardRef, Verdict
from agentic.review.v2.supervisor import (
    Arbiter, ArbiterOut, ClusterCall, Critic, CriticCall, CriticOut, PlanChoice, Planner, PlannerOut, Problem,
)
from agentic.tuning import load_profile, plan_reviewers

LAB = Path(__file__).parents[4] / "agentic_lab" / "data" / "synthetic"
CANDIDATES = plan_reviewers(load_profile(PACKAGE_ROOT / "config", "graded"), 9)


def _chat(schema, *replies):
    replies = list(replies)
    return FakeChatModel({schema: lambda _s, _u: replies.pop(0) if len(replies) > 1 else replies[0]})


def _g(code, segs=(1,), reviewer="r"):
    return Verdict(standard_code=code, verdict="growth_area", support_level="developing", segments=list(segs),
                   reviewer=reviewer)


def _refs(vs):
    return {v.standard_code: StandardRef(code=v.standard_code, concept="c", description="d", grade_min=3,
                                         grade_max=12) for v in vs}


# ---- 1: the planner sees the assignment's targets ----

@pytest.mark.skipif(not (LAB / "courses" / "2.00-control").exists(), reason="2.00 data not generated")
def test_coursedir_assignment_carries_the_curriculum_targets():
    lms = CourseDirLMSClient(LAB, "2.00-control")
    a = lms.get_assignment("classroom_2.00-control", lms_assignment_id("ela9v2-a02", "2.00-control"))
    assert a.targets == ["9-10.RL.1.A"]


def test_a_target_in_targets_forces_its_strand_and_is_shown_to_the_planner():
    Planner.clear_cache()
    skip_all = PlannerOut(choices=[PlanChoice(reviewer=s.id, action="skip", reason="No.") for s, _, _ in CANDIDATES])
    chat = _chat(PlannerOut, skip_all)
    a = Assignment(id="a-w", name="n", targets=["9-10.W.1.A.a"])
    d = Planner(chat, "$assignment $reviewers", "h").plan(a, CANDIDATES)
    assert {"3-writing", "4-writing", "5-writing"} <= set(d.run)
    assert "target 9-10.W.1.A.a" in chat.calls[0][2]


# ---- 2: a failed planner is not cached and is flagged ----

def test_a_failed_planner_is_not_cached_and_is_flagged():
    Planner.clear_cache()
    bad = PlannerOut(choices=[PlanChoice(reviewer="3-reading", action="skip", reason=" ")])
    good = PlannerOut(choices=[PlanChoice(reviewer="3-reading", action="skip", reason="Not exercised.")])
    chat = _chat(PlannerOut, bad, bad, good)
    p = Planner(chat, "$assignment $reviewers", "h")
    first = p.plan(Assignment(id="a1", name="n"), CANDIDATES)
    assert first.invalid and first.skipped == {}
    second = p.plan(Assignment(id="a1", name="n"), CANDIDATES)
    assert not second.cached and not second.invalid and second.skipped == {"3-reading": "Not exercised."}


# ---- 3: one bad critic item doesn't void the reply ----

def test_keep_with_an_outcome_is_a_keep_and_a_bad_item_is_only_that_item():
    vs = [_g("3.L.1.A.a"), _g("3.L.1.A.b"), _g("3.L.1.A.c")]
    reply = CriticOut(calls=[
        CriticCall(standard="3.L.1.A.a", action="keep", outcome="met", reason="Shown."),       # outcome ignored
        CriticCall(standard="3.L.1.A.b", action="drop", reason="No outcome given."),           # malformed: kept
        CriticCall(standard="3.L.1.A.c", action="drop", outcome="not_shown", reason="Absent.")])
    chat = _chat(CriticOut, reply)
    r = Critic(chat, "$segments $growth_areas", 60).run(vs, [], ["S."], _refs(vs))
    assert not r.invalid and len(chat.calls) == 1
    assert [v.verdict for v in r.verdicts] == ["growth_area", "growth_area", "not_evident"]


# ---- 4: one bad arbiter cluster doesn't void the others ----

def test_a_bad_cluster_is_retried_alone_and_the_good_ones_apply():
    vs = [_g("3.L.1.A.a", [1]), _g("3.L.2.B.a", [1]), _g("4.L.1.A.a", [2]), _g("4.L.2.B.a", [2])]
    first = ArbiterOut(clusters=[
        ClusterCall(cluster=1, problems=[Problem(standard="3.L.1.A.a", absorbs=["3.L.2.B.a"], reason="Same.")]),
        ClusterCall(cluster=2, problems=[Problem(standard="4.L.1.A.a", reason="Missing a member.")])])
    retry = ArbiterOut(clusters=[
        ClusterCall(cluster=1, problems=[Problem(standard="4.L.1.A.a", absorbs=["4.L.2.B.a"], reason="Same.")])])
    chat = _chat(ArbiterOut, first, retry)
    r = Arbiter(chat, "$segments $clusters", 15).run(vs, [], ["S1.", "S2."], _refs(vs))
    assert [v.verdict for v in r.verdicts] == ["growth_area", "not_evident", "growth_area", "not_evident"]
    assert "3.L.2.B.a" not in chat.calls[1][2] and "4.L.2.B.a" in chat.calls[1][2]  # only the bad cluster again
    assert not r.invalid


def test_a_cluster_bad_twice_is_left_unchanged_and_flagged():
    vs = [_g("3.L.1.A.a", [1]), _g("3.L.2.B.a", [1]), _g("4.L.1.A.a", [2]), _g("4.L.2.B.a", [2])]
    bad2 = ClusterCall(cluster=2, problems=[Problem(standard="4.L.1.A.a", reason="Missing.")])
    first = ArbiterOut(clusters=[
        ClusterCall(cluster=1, problems=[Problem(standard="3.L.1.A.a", absorbs=["3.L.2.B.a"], reason="Same.")]), bad2])
    retry = ArbiterOut(clusters=[ClusterCall(cluster=1, problems=[Problem(standard="4.L.1.A.a", reason="Missing.")])])
    r = Arbiter(_chat(ArbiterOut, first, retry), "$segments $clusters", 15).run(vs, [], ["S1.", "S2."], _refs(vs))
    assert [v.verdict for v in r.verdicts] == ["growth_area", "not_evident", "growth_area", "growth_area"]
    assert r.invalid


# ---- 5: the critic checks grade 6+ growth areas first ----

def test_critic_sends_grade_6_plus_first_when_capped():
    vs = [_g(f"3.L.1.A.{i}") for i in range(3)] + [_g("9-10.RL.1.A"), _g("8.W.3.A.b")]
    chat = _chat(CriticOut, CriticOut(calls=[]))
    r = Critic(chat, "$segments $growth_areas", 2).run(vs, [], ["S."], _refs(vs))
    sent = chat.calls[0][2]
    assert "9-10.RL.1.A" in sent and "8.W.3.A.b" in sent and "3.L.1.A.0" not in sent and r.capped


def test_supervised_profile_checks_up_to_60():
    assert load_profile(PACKAGE_ROOT / "config", "supervised").supervisor.critic_max_items == 60


# ---- Justin, 2026-11-17 (option A): a reading target keeps the grade 3-5 reading reviewers ----

@pytest.mark.parametrize("target", ["9-10.RL.1.A", "9-10.RI.1.A"])
def test_a_reading_target_forces_the_grade_3_to_5_reading_reviewers(target):
    Planner.clear_cache()
    skip_all = PlannerOut(choices=[PlanChoice(reviewer=s.id, action="skip", reason="No.") for s, _, _ in CANDIDATES])
    d = Planner(_chat(PlannerOut, skip_all), "$assignment $reviewers", "h").plan(
        Assignment(id=f"a-{target}", name="n", targets=[target]), CANDIDATES)
    assert {"3-reading", "4-reading", "5-reading"} <= set(d.run)
    assert set(d.skipped) == {"3-writing", "4-writing", "5-writing"}
