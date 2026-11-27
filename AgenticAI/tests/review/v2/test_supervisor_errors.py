"""Review supervisor, final review: a step whose model call fails changes nothing; it never fails the review.
A daily quota still stops the run (waiting will not help)."""
import pytest

from agentic.config import PACKAGE_ROOT
from agentic.llm.base import DailyQuotaExceeded
from agentic.lms.base import Assignment
from agentic.models import StandardRef, Verdict
from agentic.review.v2.supervisor import Arbiter, Critic, Planner
from agentic.tuning import load_profile, plan_reviewers


class Boom:
    model_name = "boom"

    def __init__(self, error):
        self.error, self.calls = error, 0

    def complete(self, system, user, schema):
        self.calls += 1
        raise self.error


VS = [Verdict(standard_code="6.W.3.A.b", verdict="growth_area", support_level="developing", segments=[1], reviewer="r"),
      Verdict(standard_code="6.L.1.A.a", verdict="growth_area", support_level="developing", segments=[1], reviewer="r")]
REFS = {v.standard_code: StandardRef(code=v.standard_code, concept="c", description="d", grade_min=6, grade_max=6)
        for v in VS}


@pytest.mark.parametrize("make", [lambda c: Critic(c, "$segments $growth_areas", 30),
                                  lambda c: Arbiter(c, "$segments $clusters", 15)])
def test_a_failing_call_leaves_the_verdicts_and_flags_invalid(make):
    chat = Boom(RuntimeError("Secret essay text in an error"))
    r = make(chat).run(list(VS), [], ["S one."], REFS)
    assert r.invalid and r.verdicts == VS and chat.calls == 2


def test_a_failing_planner_runs_everything():
    Planner.clear_cache()
    chat = Boom(ValueError("bad json"))
    cands = plan_reviewers(load_profile(PACKAGE_ROOT / "config", "graded"), 9)
    d = Planner(chat, "$assignment $reviewers", "h").plan(Assignment(id="a", name="n"), cands)
    assert d.skipped == {} and chat.calls == 2


def test_daily_quota_still_stops_the_run():
    with pytest.raises(DailyQuotaExceeded):
        Critic(Boom(DailyQuotaExceeded()), "$segments $growth_areas", 30).run(list(VS), [], ["S one."], REFS)
