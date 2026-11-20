"""Review supervisor, Task 2: the planner chooses which grade 3-5 writing and reading reviewers run."""
from agentic.config import PACKAGE_ROOT
from agentic.llm.fake import FakeChatModel
from agentic.lms.base import Assignment
from agentic.review.v2.supervisor import PlanChoice, Planner, PlannerOut
from agentic.tuning import load_profile, plan_reviewers

CANDIDATES = plan_reviewers(load_profile(PACKAGE_ROOT / "config", "graded"), 9)
SKIPPABLE = {"3-writing", "4-writing", "5-writing", "3-reading", "4-reading", "5-reading"}
ALWAYS = {"3-language", "4-language", "5-language", "6+-writing", "6+-reading"}


def _assignment(id_="a1", notes=None, description="Write one paragraph about Rainsford."):
    return Assignment(id=id_, name="Rainsford", description=description, notes=notes or {},
                      submission_type="paragraph", reading_strand="RL")


def _chat(*replies):
    replies = list(replies)
    return FakeChatModel({PlannerOut: lambda _s, _u: replies.pop(0) if len(replies) > 1 else replies[0]})


def _skip_all():
    return PlannerOut(choices=[PlanChoice(reviewer=s.id, action="skip", reason="Not exercised.")
                               for s, _, _ in CANDIDATES])


def _planner(chat, h="h"):
    Planner.clear_cache()
    return Planner(chat, "Plan.\n$assignment\n$reviewers", h)


def test_planner_can_skip_only_grade_3_to_5_writing_and_reading():
    d = _planner(_chat(_skip_all())).plan(_assignment(), CANDIDATES)
    assert set(d.skipped) == SKIPPABLE
    assert ALWAYS <= set(d.run) and not set(d.run) & set(d.skipped)


def test_a_target_strand_forces_its_reviewers_to_run():
    d = _planner(_chat(_skip_all())).plan(_assignment(notes={"4.W.1.A.a": "claims"}), CANDIDATES)
    assert {"3-writing", "4-writing", "5-writing"} <= set(d.run)
    assert set(d.skipped) == {"3-reading", "4-reading", "5-reading"}


def test_omitted_or_unknown_reviewers_default_to_run():
    reply = PlannerOut(choices=[PlanChoice(reviewer="nope", action="skip", reason="x")])
    d = _planner(_chat(reply)).plan(_assignment(), CANDIDATES)
    assert d.skipped == {} and set(d.run) == {s.id for s, _, _ in CANDIDATES}


def test_one_call_per_assignment_and_tuning_hash():
    chat = _chat(_skip_all())
    p = _planner(chat, "hash-a")
    first = p.plan(_assignment("a1"), CANDIDATES)
    again = p.plan(_assignment("a1"), CANDIDATES)
    assert len(chat.calls) == 1 and again.cached and not first.cached and again.usage is None
    assert again.skipped == first.skipped
    Planner(chat, "T $assignment $reviewers", "hash-b").plan(_assignment("a1"), CANDIDATES)  # new tuning
    assert len(chat.calls) == 2


def test_prompt_holds_the_assignment_and_candidates():
    chat = _chat(_skip_all())
    _planner(chat).plan(_assignment(description="Describe what kind of person Rainsford is."), CANDIDATES)
    user = chat.calls[0][2]
    assert "Describe what kind of person Rainsford is." in user and "3-writing" in user and "6+-reading" in user


def test_long_reason_is_cut_to_12_words():
    long = " ".join(f"w{i}" for i in range(20))
    reply = PlannerOut(choices=[PlanChoice(reviewer="3-reading", action="skip", reason=long)])
    d = _planner(_chat(reply)).plan(_assignment(), CANDIDATES)
    assert d.skipped["3-reading"] == " ".join(f"w{i}" for i in range(12))


def test_an_invalid_reply_twice_runs_everything():
    bad = PlannerOut(choices=[PlanChoice(reviewer="3-reading", action="skip", reason="")])  # empty reason
    chat = _chat(bad)
    d = _planner(chat).plan(_assignment(), CANDIDATES)
    assert d.skipped == {} and len(chat.calls) == 2
