import re

import pytest
from pydantic import BaseModel

from agentic.config import PACKAGE_ROOT
from agentic.llm.base import DailyQuotaExceeded
from agentic.llm.fake import FakeChatModel, FakeEmbedder
from agentic.models import StandardRef
from agentic.review.v2.bands import BandReviewer
from agentic.review.v2.schemas import (
    PerStandardOut, ProgressionJudgment, ProgressionOut, SegmentEvidence, StandardJudgment, StepJudgment,
    StepLevel, StepOut,
)
from agentic.review.v2.scope import BandScope
from agentic.standards.fake import InMemoryStandards
from agentic.tuning import ReviewerSpec, load_prompt

PROMPTS = PACKAGE_ROOT / "prompts"
SEGMENTS = ["First paragraph.", "Second paragraph.", "Third paragraph."]


def _std(code, lo, hi, text=None):
    return StandardRef(code=code, concept="c", description="d", grade_min=lo, grade_max=hi,
                       strand=code.split(".")[1], sub_expectation=text or code)


def _src(*refs):
    return InMemoryStandards(list(refs), FakeEmbedder())


PER_STD = [_std("3.L.1.A.a", 3, 3), _std("4.L.1.A.a", 4, 4), _std("5.L.1.A.a", 5, 5)]
# grade-9 course, writing band 6..10: steps 6, 7, 8, 9 (9-10) in one group; 7 has two codes
PROG = [_std("6.W.2.A.a", 6, 6, "six"), _std("7.W.2.A.a", 7, 7, "seven a"),
        _std("7.W.2.A.b", 7, 7, "seven b"), _std("8.W.2.A.a", 8, 8, "eight"),
        _std("9-10.W.2.A.a", 9, 10, "nine")]


def _per_standard(chat, refs=PER_STD):
    spec = ReviewerSpec(id="3-5-L", grades=(3, 5), strands=("L",), mode="per_standard",
                        prompt="per_standard.v1")
    return BandReviewer(spec, 3, 5, BandScope(_src(*refs), 3, 5, ["L"]), chat,
                        load_prompt(PROMPTS, "per_standard.v1"))


def _progression(chat, refs=PROG, prompt="progression.v1"):
    spec = ReviewerSpec(id="6-G-W", grades=(6, "course"), strands=("W",), mode="progression",
                        prompt=prompt)
    return BandReviewer(spec, 6, 10, BandScope(_src(*refs), 6, 10, ["W"]), chat,
                        load_prompt(PROMPTS, prompt))


def _codes(user):
    return re.findall(r"^- (\S+) \|", user, flags=re.M)


def _groups(user):
    return re.findall(r"^## (\S+)", user, flags=re.M)


def _ev(seg=1, reason="ideas lack support"):
    return [SegmentEvidence(segment=seg, reason=reason)]


def _meets(code, **kw):
    return StandardJudgment(standard_code=code, verdict="meets", support_level=None, **kw)


def _run(reviewer, strengths=True):
    return reviewer.review(SEGMENTS, "paragraph", None, strengths)


def _reasons(result):
    return [d.reason for d in result.dropped]


# ---- zero standards ----

def test_zero_standards_makes_no_call():
    chat = FakeChatModel({})
    r = _run(_per_standard(chat, refs=[]))
    assert chat.calls == []
    assert (r.verdicts, r.observations, r.dropped, r.calls, r.standards_count) == ([], [], [], 0, 0)
    assert r.usage.input_tokens == 0 and r.reviewer_id == "3-5-L" and r.prompt_version == "per_standard.v1"


# ---- per standard ----

def test_per_standard_prompt_lists_standards_before_segments():
    chat = FakeChatModel({PerStandardOut: lambda s, u: PerStandardOut(
        judgments=[_meets(c) for c in _codes(u)])})
    _run(_per_standard(chat))
    (_, _, user), = chat.calls
    assert _codes(user) == ["3.L.1.A.a", "4.L.1.A.a", "5.L.1.A.a"]
    assert "[S1] First paragraph." in user and "[S3] Third paragraph." in user
    assert user.index("- 3.L.1.A.a |") < user.index("[S1]")
    assert "$" not in user


def test_missing_codes_get_one_follow_up_then_unjudged():
    replies = iter([PerStandardOut(judgments=[_meets("3.L.1.A.a")]),
                    PerStandardOut(judgments=[_meets("4.L.1.A.a")])])
    chat = FakeChatModel({PerStandardOut: lambda s, u: next(replies)})
    r = _run(_per_standard(chat))
    assert len(chat.calls) == 2 and r.calls == 2
    assert _codes(chat.calls[1][2]) == ["4.L.1.A.a", "5.L.1.A.a"]
    assert [v.standard_code for v in r.verdicts] == ["3.L.1.A.a", "4.L.1.A.a"]
    assert _reasons(r) == ["unjudged"] and r.dropped[0].stage == "band:3-5-L"
    assert r.usage.input_tokens > 0 and r.usage.input_tokens == sum(
        (len(s) + len(u)) // 4 for _, s, u in chat.calls)


def test_duplicate_and_unknown_codes_are_dropped():
    chat = FakeChatModel({PerStandardOut: lambda s, u: PerStandardOut(judgments=[
        _meets("3.L.1.A.a"),
        StandardJudgment(standard_code="3.L.1.A.a", verdict="growth_area", support_level="beginning",
                         evidence=_ev()),
        _meets("9.X.1.A.a"), _meets("4.L.1.A.a"), _meets("5.L.1.A.a")])})
    r = _run(_per_standard(chat))
    assert len(chat.calls) == 1
    assert [(v.standard_code, v.verdict) for v in r.verdicts] == [
        ("3.L.1.A.a", "meets"), ("4.L.1.A.a", "meets"), ("5.L.1.A.a", "meets")]
    assert _reasons(r) == ["duplicate_judgment:3.L.1.A.a", "unknown_standard"]


def test_per_standard_guards_and_observations():
    long = "one two three four five six seven eight nine ten eleven twelve thirteen"
    chat = FakeChatModel({PerStandardOut: lambda s, u: PerStandardOut(judgments=[
        StandardJudgment(standard_code="3.L.1.A.a", verdict="growth_area", support_level=None,
                         evidence=_ev()),
        StandardJudgment(standard_code="4.L.1.A.a", verdict="growth_area", support_level="developing",
                         evidence=[SegmentEvidence(segment=2, reason="verb tense shifts"),
                                   SegmentEvidence(segment=4, reason="out of range"),
                                   SegmentEvidence(segment=3, reason=long)]),
        StandardJudgment(standard_code="5.L.1.A.a", verdict="not_evident", support_level=None)])})
    r = _run(_per_standard(chat))
    assert len(chat.calls) == 1  # dropped entries are not re-asked
    assert [(v.standard_code, v.verdict, v.support_level, v.segments) for v in r.verdicts] == [
        ("4.L.1.A.a", "growth_area", "developing", [2]), ("5.L.1.A.a", "not_evident", None, [])]
    assert _reasons(r) == ["no_support_level:3.L.1.A.a", "bad_segment:4", "reason_too_long:4.L.1.A.a"]
    (o,) = r.observations
    assert (o.standard_code, o.paragraph, o.reason, o.severity, o.level) == (
        "4.L.1.A.a", 2, "verb tense shifts", "developing", "paragraph")
    assert all(v.reviewer == "3-5-L" for v in r.verdicts)


def test_growth_area_with_no_valid_evidence_is_dropped():
    chat = FakeChatModel({PerStandardOut: lambda s, u: PerStandardOut(judgments=[
        StandardJudgment(standard_code="3.L.1.A.a", verdict="growth_area", support_level="beginning",
                         evidence=_ev(seg=9)), _meets("4.L.1.A.a"), _meets("5.L.1.A.a")])})
    r = _run(_per_standard(chat))
    assert [v.standard_code for v in r.verdicts] == ["4.L.1.A.a", "5.L.1.A.a"]
    assert _reasons(r) == ["bad_segment:9", "no_valid_evidence:3.L.1.A.a"]


def test_strengths_kept_stripped_or_dropped():
    long_note = " ".join(["word"] * 41)
    reply = lambda s, u: PerStandardOut(judgments=[  # noqa: E731
        _meets("3.L.1.A.a", notable_strength=True, strength_note="varied verbs used well"),
        _meets("4.L.1.A.a", notable_strength=True, strength_note=long_note),
        _meets("5.L.1.A.a")])
    r = _run(_per_standard(FakeChatModel({PerStandardOut: reply})))
    assert [(v.notable_strength, v.strength_note) for v in r.verdicts] == [
        (True, "varied verbs used well"), (False, None), (False, None)]
    assert _reasons(r) == ["strength_note_too_long:4.L.1.A.a"]
    off = _run(_per_standard(FakeChatModel({PerStandardOut: reply})), strengths=False)
    assert [(v.notable_strength, v.strength_note) for v in off.verdicts] == [(False, None)] * 3
    assert off.dropped == []


def test_strength_without_a_note_is_dropped_and_the_verdict_kept():
    chat = FakeChatModel({PerStandardOut: lambda s, u: PerStandardOut(judgments=[
        _meets("3.L.1.A.a", notable_strength=True),
        _meets("4.L.1.A.a", notable_strength=True, strength_note="   "),
        _meets("5.L.1.A.a")])})
    r = _run(_per_standard(chat))
    assert [(v.standard_code, v.verdict, v.notable_strength, v.strength_note) for v in r.verdicts] == [
        ("3.L.1.A.a", "meets", False, None), ("4.L.1.A.a", "meets", False, None),
        ("5.L.1.A.a", "meets", False, None)]
    assert _reasons(r) == ["strength_without_note:3.L.1.A.a", "strength_without_note:4.L.1.A.a"]
    assert {d.stage for d in r.dropped} == {"band:3-5-L"}


def test_progression_strength_without_a_note_is_dropped():
    chat = FakeChatModel({ProgressionOut: _prog_reply(7, notable_strength=True)})
    r = _run(_progression(chat))
    assert len(r.verdicts) == 5 and not any(v.notable_strength for v in r.verdicts)
    assert _reasons(r) == ["strength_without_note:W.2.A"]


def test_invalid_schema_twice_then_valid_makes_three_calls():
    n = {"calls": 0}

    def handler(s, u):
        n["calls"] += 1
        if n["calls"] < 3:
            return PerStandardOut.model_validate({"judgments": "not a list"})
        return PerStandardOut(judgments=[_meets(c) for c in _codes(u)])

    chat = FakeChatModel({PerStandardOut: handler})
    r = _run(_per_standard(chat))
    assert len(chat.calls) == 3 and r.calls == 3 and len(r.verdicts) == 3


def test_invalid_three_times_raises():
    chat = FakeChatModel({PerStandardOut: lambda s, u: PerStandardOut.model_validate({})})
    with pytest.raises(ValueError):  # pydantic ValidationError
        _run(_per_standard(chat))
    assert len(chat.calls) == 3


def test_daily_quota_propagates_without_retry():
    def handler(s, u):
        raise DailyQuotaExceeded()

    chat = FakeChatModel({PerStandardOut: handler})
    with pytest.raises(DailyQuotaExceeded):
        _run(_per_standard(chat))
    assert len(chat.calls) == 1


# ---- progression ----

def _prog_reply(working_grade, support="developing", evidence=None, **kw):
    def handler(s, u):
        return ProgressionOut(progressions=[ProgressionJudgment(
            group=g, working_grade=working_grade, support_level=support,
            evidence=evidence if evidence is not None else _ev(), **kw) for g in _groups(u)])
    return handler


def _by_code(result):
    return {v.standard_code: v for v in result.verdicts}


def test_progression_prompt_groups_steps():
    chat = FakeChatModel({ProgressionOut: _prog_reply(7)})
    _run(_progression(chat))
    (_, _, user), = chat.calls
    assert _groups(user) == ["W.2.A"]
    assert "- 7.W.2.A.b (grade 7) | seven b" in user
    assert "- 9-10.W.2.A.a (grade 9) | nine" in user
    assert user.index("## W.2.A") < user.index("[S1]")


def test_working_grade_7_in_grade_9_course():
    chat = FakeChatModel({ProgressionOut: _prog_reply(
        7, evidence=[SegmentEvidence(segment=2, reason="claims lack evidence"),
                     SegmentEvidence(segment=3, reason="conclusion restates intro")],
        notable_strength=True, strength_note="clear topic sentences")})
    r = _run(_progression(chat, refs=PROG))
    v = _by_code(r)
    assert [(c, v[c].verdict, v[c].support_level) for c in sorted(v)] == [
        ("6.W.2.A.a", "meets", None), ("7.W.2.A.a", "meets", None), ("7.W.2.A.b", "meets", None),
        ("8.W.2.A.a", "growth_area", "developing"), ("9-10.W.2.A.a", "growth_area", "developing")]
    assert all(x.working_grade == 7 for x in r.verdicts)
    assert {c for c in v if v[c].notable_strength} == {"7.W.2.A.a", "7.W.2.A.b"}
    assert v["7.W.2.A.a"].strength_note == "clear topic sentences"
    assert sorted((o.standard_code, o.paragraph) for o in r.observations) == [
        ("8.W.2.A.a", 2), ("8.W.2.A.a", 3), ("9-10.W.2.A.a", 2), ("9-10.W.2.A.a", 3)]
    assert all(o.severity == "developing" and o.level == "paragraph" for o in r.observations)
    assert r.dropped == [] and r.calls == 1 and r.standards_count == 5


def test_working_grade_not_a_step_is_dropped():
    chat = FakeChatModel({ProgressionOut: _prog_reply(5)})
    r = _run(_progression(chat, refs=PROG))
    assert r.verdicts == [] and r.observations == []
    assert _reasons(r) == ["bad_working_grade:W.2.A"]
    assert len(chat.calls) == 1  # answered, so no follow-up


def test_working_grade_none_makes_every_step_a_growth_area():
    chat = FakeChatModel({ProgressionOut: _prog_reply(None, support="beginning",
                                                      notable_strength=True, strength_note="x y")})
    r = _run(_progression(chat, refs=PROG))
    assert {v.verdict for v in r.verdicts} == {"growth_area"} and len(r.verdicts) == 5
    assert all(v.working_grade is None and v.support_level == "beginning" for v in r.verdicts)
    assert not any(v.notable_strength for v in r.verdicts)
    assert len(r.observations) == 5


def test_progression_not_evident_needs_no_evidence():
    chat = FakeChatModel({ProgressionOut: _prog_reply(8, support="beginning", evident=False,
                                                      notable_strength=True, strength_note="good")})
    r = _run(_progression(chat, refs=PROG))
    assert [(v.verdict, v.support_level, v.working_grade, v.notable_strength, v.strength_note, v.segments)
            for v in r.verdicts] == [("not_evident", None, None, False, None, [])] * 5
    assert r.observations == [] and r.dropped == [] and r.calls == 1
    bare = FakeChatModel({ProgressionOut: lambda s, u: ProgressionOut(progressions=[
        ProgressionJudgment(group="W.2.A", evident=False)])})
    r = _run(_progression(bare, refs=PROG))
    assert {v.verdict for v in r.verdicts} == {"not_evident"} and r.dropped == []


def test_top_step_met_has_no_observations():
    chat = FakeChatModel({ProgressionOut: _prog_reply(9, support=None)})
    r = _run(_progression(chat, refs=PROG))
    assert {v.verdict for v in r.verdicts} == {"meets"} and r.observations == []
    assert all(v.segments == [1] for v in r.verdicts)


def test_progression_growth_without_support_level_is_dropped():
    chat = FakeChatModel({ProgressionOut: _prog_reply(7, support=None)})
    r = _run(_progression(chat, refs=PROG))
    assert r.verdicts == [] and _reasons(r) == ["missing_step_level:W.2.A"]  # was no_support_level before progression.v2


def test_missing_group_follow_up_and_unknown_group_dropped():
    refs = [_std("6.W.1.A.a", 6, 6, "a6"), _std("7.W.1.A.a", 7, 7, "a7"),
            _std("6.W.2.A.a", 6, 6, "b6"), _std("7.W.2.A.a", 7, 7, "b7")]
    replies = iter([
        ProgressionOut(progressions=[
            ProgressionJudgment(group="W.1.A", working_grade=7, support_level=None),
            ProgressionJudgment(group="W.9.Z", working_grade=7, support_level=None),
            ProgressionJudgment(group="W.1.A", working_grade=6, support_level="approaching",
                                evidence=_ev())]),
        ProgressionOut(progressions=[])])
    chat = FakeChatModel({ProgressionOut: lambda s, u: next(replies)})
    r = _run(_progression(chat, refs=refs))
    assert len(chat.calls) == 2 and _groups(chat.calls[1][2]) == ["W.2.A"]
    assert sorted(v.standard_code for v in r.verdicts) == ["6.W.1.A.a", "7.W.1.A.a"]
    assert _reasons(r) == ["unknown_group", "duplicate_judgment:W.1.A", "unjudged"]
    assert "follow_up" in r.raw


def test_progression_strengths_stripped_when_disabled():
    chat = FakeChatModel({ProgressionOut: _prog_reply(7, notable_strength=True, strength_note="good")})
    r = _run(_progression(chat, refs=PROG), strengths=False)
    assert not any(v.notable_strength for v in r.verdicts)


def test_schema_models_are_pydantic():
    for m in (SegmentEvidence, StandardJudgment, PerStandardOut, ProgressionJudgment, ProgressionOut):
        assert issubclass(m, BaseModel)


def test_prompts_describe_their_reply_fields():
    for name in ("per_standard.v1", "progression.v1"):
        text = load_prompt(PROMPTS, name)
        assert "notable_strength" in text and "strength_note" in text and "1 to 40 words" in text
        assert "discarded" in text
    progression = load_prompt(PROMPTS, "progression.v1")
    assert "working_grade" in progression and "evident" in progression and "null" in progression


# ---- per-step support levels (progression.v2) ----

def _steps_reply(working_grade, steps, support=None, **kw):
    def handler(s, u):
        return ProgressionOut(progressions=[ProgressionJudgment(
            group=g, working_grade=working_grade, support_level=support, evidence=_ev(),
            step_levels=[StepLevel(grade=gr, support_level=lv) for gr, lv in steps], **kw)
            for g in _groups(u)])
    return handler


def _levels(r):
    return {v.standard_code: v.support_level for v in r.verdicts if v.verdict == "growth_area"}


def test_each_unmet_step_gets_its_own_level():
    chat = FakeChatModel({ProgressionOut: _steps_reply(
        None, [(6, "approaching"), (7, "developing"), (8, "developing"), (9, "beginning")])})
    r = _run(_progression(chat, prompt="progression.v2"))
    assert _levels(r) == {"6.W.2.A.a": "approaching", "7.W.2.A.a": "developing", "7.W.2.A.b": "developing",
                          "8.W.2.A.a": "developing", "9-10.W.2.A.a": "beginning"}
    assert {o.standard_code: o.severity for o in r.observations} == _levels(r)  # findings read these
    assert r.dropped == []


def test_merged_step_gives_all_its_codes_one_level():
    chat = FakeChatModel({ProgressionOut: _steps_reply(6, [(7, "beginning"), (8, "developing"),
                                                           (9, "developing")])})
    r = _run(_progression(chat, prompt="progression.v2"))
    lv = _levels(r)
    assert lv["7.W.2.A.a"] == lv["7.W.2.A.b"] == "beginning"
    assert {o.severity for o in r.observations if o.standard_code.startswith("7.")} == {"beginning"}


def test_step_without_an_entry_takes_support_level():
    chat = FakeChatModel({ProgressionOut: _steps_reply(6, [(7, "approaching")], support="developing")})
    r = _run(_progression(chat, prompt="progression.v2"))
    lv = _levels(r)
    assert lv["7.W.2.A.a"] == "approaching" and lv["8.W.2.A.a"] == lv["9-10.W.2.A.a"] == "developing"
    assert _reasons(r) == []


def test_step_without_any_level_drops_the_group():
    chat = FakeChatModel({ProgressionOut: _steps_reply(6, [(7, "developing")])})
    r = _run(_progression(chat, prompt="progression.v2"))
    assert r.verdicts == [] and r.observations == []
    assert _reasons(r) == ["missing_step_level:W.2.A"]


def test_bad_step_entries_are_ignored_and_duplicates_keep_the_first():
    chat = FakeChatModel({ProgressionOut: _steps_reply(
        7, [(7, "beginning"), (12, "beginning"), (8, "approaching"), (8, "beginning"),
            (9, "developing")])})
    r = _run(_progression(chat, prompt="progression.v2"))
    assert _levels(r) == {"8.W.2.A.a": "approaching", "9-10.W.2.A.a": "developing"}
    assert _reasons(r) == ["bad_step_level:W.2.A"] * 3  # a meets step, an unknown grade, a duplicate
    assert all(v.verdict == "meets" for v in r.verdicts if v.standard_code.startswith(("6.", "7.")))


def test_inversion_is_flagged_and_levels_are_kept():
    chat = FakeChatModel({ProgressionOut: _steps_reply(6, [(7, "beginning"), (8, "approaching"),
                                                           (9, "approaching")])})
    r = _run(_progression(chat, prompt="progression.v2"))
    lv = _levels(r)
    assert lv["7.W.2.A.a"] == "beginning" and lv["8.W.2.A.a"] == "approaching"
    assert [(d.stage, d.reason) for d in r.dropped] == [("band:6-G-W", "level_inversion:W.2.A")]


def test_levels_that_rise_or_stay_are_not_flagged():
    chat = FakeChatModel({ProgressionOut: _steps_reply(6, [(7, "developing"), (8, "developing"),
                                                           (9, "beginning")])})
    assert _reasons(_run(_progression(chat, prompt="progression.v2"))) == []


def test_v1_shape_stays_flat():
    chat = FakeChatModel({ProgressionOut: _prog_reply(7)})
    r = _run(_progression(chat))
    assert _levels(r) == {"8.W.2.A.a": "developing", "9-10.W.2.A.a": "developing"} and r.dropped == []


def test_v2_prompt_asks_for_step_levels():
    text = load_prompt(PROMPTS, "progression.v2")
    assert "step_levels" in text and "notable_strength" in text and "1 to 40 words" in text
    assert "evident" in text and "working_grade" in text
    assert "step_levels" not in load_prompt(PROMPTS, "progression.v1")


# ---- downward search (mode search, progression.v3) ----

def _search(chat, refs=PROG, prompt="progression.v3"):
    spec = ReviewerSpec(id="6-G-W", grades=(6, "course"), strands=("W",), mode="search", prompt=prompt)
    return BandReviewer(spec, 6, 10, BandScope(_src(*refs), 6, 10, ["W"]), chat,
                        load_prompt(PROMPTS, prompt))


def _asked(user):
    """group -> the codes shown for it in one call."""
    out = {}
    for block in re.split(r"^## ", user, flags=re.M)[1:]:
        out[block.split(None, 1)[0]] = re.findall(r"^- (\S+) \(grade \d+\) \|", block, flags=re.M)
    return out


def _step(group, verdict, level=None, evidence=None, **kw):
    if evidence is None:
        evidence = _ev() if verdict == "growth_area" else []
    return StepJudgment(group=group, verdict=verdict, support_level=level, evidence=evidence, **kw)


def _rounds(*replies):
    """One scripted reply per call: each is a list of StepJudgment or a function of the user text."""
    it = iter(replies)

    def handler(s, u):
        reply = next(it)
        return StepOut(judgments=reply(u) if callable(reply) else reply)
    return handler


def _summary(r):
    return [(v.standard_code, v.verdict, v.support_level, v.working_grade, v.segments)
            for v in sorted(r.verdicts, key=lambda v: v.standard_code)]


def test_search_walks_down_to_the_first_met_step():
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.2.A", "growth_area", "beginning", _ev(2))],
        [_step("W.2.A", "growth_area", "developing", _ev(1))],
        [_step("W.2.A", "meets", evidence=_ev(3, "claims are supported"))])})
    r = _run(_search(chat))
    assert [_asked(u) for _, _, u in chat.calls] == [
        {"W.2.A": ["9-10.W.2.A.a"]}, {"W.2.A": ["8.W.2.A.a"]}, {"W.2.A": ["7.W.2.A.a", "7.W.2.A.b"]}]
    assert _summary(r) == [
        ("6.W.2.A.a", "meets", None, 7, []), ("7.W.2.A.a", "meets", None, 7, [3]),
        ("7.W.2.A.b", "meets", None, 7, [3]), ("8.W.2.A.a", "growth_area", "developing", 7, [1]),
        ("9-10.W.2.A.a", "growth_area", "beginning", 7, [2])]
    assert sorted((o.standard_code, o.paragraph, o.severity) for o in r.observations) == [
        ("8.W.2.A.a", 1, "developing"), ("9-10.W.2.A.a", 2, "beginning")]
    assert (r.calls, r.rounds, r.dropped, r.standards_count) == (3, 3, [], 5)
    assert r.usage.calls == 3 and r.usage.input_tokens == sum((len(s) + len(u)) // 4 for _, s, u in chat.calls)
    assert len(r.raw["rounds"]) == 3


def test_search_met_at_the_top_step_makes_one_call():
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.2.A", "meets", evidence=_ev(1, "claims are supported"), notable_strength=True,
               strength_note="precise claims throughout")])})
    r = _run(_search(chat))
    assert len(chat.calls) == 1 and r.rounds == 1
    assert {v.verdict for v in r.verdicts} == {"meets"} and all(v.working_grade == 9 for v in r.verdicts)
    v = _by_code(r)
    assert v["9-10.W.2.A.a"].segments == [1] and v["8.W.2.A.a"].segments == []
    assert {c for c in v if v[c].notable_strength} == {"9-10.W.2.A.a"}
    assert v["9-10.W.2.A.a"].strength_note == "precise claims throughout"
    assert r.observations == [] and r.dropped == []


def test_search_nothing_met_ends_at_grade_6_below_the_band():
    chat = FakeChatModel({StepOut: _rounds(
        *[[_step("W.2.A", "growth_area", "beginning")]] * 4)})
    r = _run(_search(chat))
    assert len(chat.calls) == 4 and r.rounds == 4
    assert _asked(chat.calls[3][2]) == {"W.2.A": ["6.W.2.A.a"]}
    assert {v.verdict for v in r.verdicts} == {"growth_area"} and len(r.verdicts) == 5
    assert all(v.working_grade is None for v in r.verdicts)
    assert len({o.standard_code for o in r.observations}) == 5 and r.dropped == []


def test_search_not_evident_at_the_top_stops_the_group():
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.2.A", "not_evident", evidence=_ev(), notable_strength=True, strength_note="good")])})
    r = _run(_search(chat))
    assert len(chat.calls) == 1
    assert [(v.verdict, v.support_level, v.working_grade, v.notable_strength, v.segments)
            for v in r.verdicts] == [("not_evident", None, None, False, [])] * 5
    assert r.observations == [] and _reasons(r) == ["strength_not_met:W.2.A"]


def test_search_not_evident_lower_down_keeps_the_growth_above():
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.2.A", "growth_area", "developing")], [_step("W.2.A", "not_evident")])})
    r = _run(_search(chat))
    assert len(chat.calls) == 2
    assert _summary(r) == [
        ("6.W.2.A.a", "not_evident", None, None, []), ("7.W.2.A.a", "not_evident", None, None, []),
        ("7.W.2.A.b", "not_evident", None, None, []), ("8.W.2.A.a", "not_evident", None, None, []),
        ("9-10.W.2.A.a", "growth_area", "developing", None, [1])]
    assert _reasons(r) == ["search_incomplete:W.2.A"]  # not "below the band": the search never ended


def test_search_treats_a_merged_step_as_one():
    refs = [_std("6.W.2.A.a", 6, 6, "six"), _std("7.W.2.A.a", 7, 7, "same"),
            _std("8.W.2.A.a", 8, 8, "same"), _std("9-10.W.2.A.a", 9, 10, "nine")]
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.2.A", "growth_area", "developing")], [_step("W.2.A", "meets")])})
    r = _run(_search(chat, refs=refs))
    assert len(chat.calls) == 2
    assert _asked(chat.calls[1][2]) == {"W.2.A": ["7.W.2.A.a", "8.W.2.A.a"]}
    assert "- 8.W.2.A.a (grade 7) |" in chat.calls[1][2]  # the merged step shows its grade
    v = _by_code(r)
    assert v["7.W.2.A.a"].verdict == v["8.W.2.A.a"].verdict == "meets"
    assert all(x.working_grade == 7 for x in r.verdicts)


TWO = [_std("6.W.1.A.a", 6, 6, "a6"), _std("7.W.1.A.a", 7, 7, "a7"),
       _std("6.W.2.A.a", 6, 6, "b6"), _std("7.W.2.A.a", 7, 7, "b7")]


def test_search_omitted_group_gets_one_follow_up_within_its_round():
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.1.A", "growth_area", "developing")],
        [_step("W.2.A", "meets")],
        [_step("W.1.A", "meets")])})
    r = _run(_search(chat, refs=TWO))
    assert [_asked(u) for _, _, u in chat.calls] == [
        {"W.1.A": ["7.W.1.A.a"], "W.2.A": ["7.W.2.A.a"]}, {"W.2.A": ["7.W.2.A.a"]}, {"W.1.A": ["6.W.1.A.a"]}]
    assert (r.calls, r.rounds) == (3, 2)
    assert "follow_up" in r.raw["rounds"][0]
    assert _summary(r) == [
        ("6.W.1.A.a", "meets", None, 6, []), ("6.W.2.A.a", "meets", None, 7, []),
        ("7.W.1.A.a", "growth_area", "developing", 6, [1]), ("7.W.2.A.a", "meets", None, 7, [])]
    assert r.dropped == []


def test_search_asks_each_step_once_and_calls_at_most_the_steps():
    # W.1.A: 2 steps, met at 6; W.2.A: 4 steps, none met. Rounds batch both groups.
    refs = TWO[:2] + PROG
    chat = FakeChatModel({StepOut: _rounds(
        lambda u: [_step(g, "growth_area", "beginning") for g in _asked(u)],
        lambda u: [_step(g, "meets" if g == "W.1.A" else "growth_area", "beginning") for g in _asked(u)],
        lambda u: [_step(g, "growth_area", "beginning") for g in _asked(u)],
        lambda u: [_step(g, "growth_area", "beginning") for g in _asked(u)])})
    r = _run(_search(chat, refs=refs))
    assert len(chat.calls) == r.calls == r.rounds == 4
    asked = [c for _, _, u in chat.calls for codes in _asked(u).values() for c in codes]
    assert len(asked) == len(set(asked)) == 7  # every code once; 7.W.2.A.a and .b are one step
    assert list(_asked(chat.calls[1][2])) == ["W.1.A", "W.2.A"]
    assert list(_asked(chat.calls[2][2])) == ["W.2.A"]


def test_search_unknown_duplicate_and_unjudged_groups_are_dropped():
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.1.A", "meets"), _step("W.9.Z", "meets"), _step("W.1.A", "growth_area", "beginning")],
        [])})
    r = _run(_search(chat, refs=TWO))
    assert len(chat.calls) == 2 and _asked(chat.calls[1][2]) == {"W.2.A": ["7.W.2.A.a"]}
    assert sorted(v.standard_code for v in r.verdicts) == ["6.W.1.A.a", "7.W.1.A.a"]
    assert _reasons(r) == ["unknown_group", "duplicate_judgment:W.1.A", "unjudged:W.2.A",
                           "search_incomplete:W.2.A"]
    assert {d.stage for d in r.dropped} == {"band:6-G-W"}


INCOMPLETE = "search_incomplete:W.2.A"


@pytest.mark.parametrize("judgment, reasons", [
    (_step("W.2.A", "maybe"), ["bad_verdict:W.2.A", INCOMPLETE]),
    (_step("W.2.A", "growth_area", None), ["no_support_level:W.2.A", INCOMPLETE]),
    (_step("W.2.A", "growth_area", "severe"), ["bad_support_level:W.2.A", INCOMPLETE]),
    (_step("W.2.A", "growth_area", "beginning", [SegmentEvidence(segment=7, reason="out of range")]),
     ["bad_segment:7", "no_valid_evidence:W.2.A", INCOMPLETE]),
    (_step("W.2.A", "growth_area", "beginning", [SegmentEvidence(segment=1, reason=" ")]),
     ["reason_blank:W.2.A", "no_valid_evidence:W.2.A", INCOMPLETE]),
    (_step("W.2.A", "growth_area", "beginning", [SegmentEvidence(segment=1, reason=" ".join(["w"] * 13))]),
     ["reason_too_long:W.2.A", "no_valid_evidence:W.2.A", INCOMPLETE]),
])
def test_search_a_bad_top_judgment_drops_the_group_with_its_reason(judgment, reasons):
    chat = FakeChatModel({StepOut: _rounds([judgment])})
    r = _run(_search(chat))
    assert len(chat.calls) == 1 and r.verdicts == [] and r.observations == []
    assert _reasons(r) == reasons


def test_search_a_bad_lower_judgment_keeps_the_growth_already_found():
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.2.A", "growth_area", "developing")], [_step("W.2.A", "growth_area", None)])})
    r = _run(_search(chat))
    assert len(chat.calls) == 2
    assert _summary(r) == [("9-10.W.2.A.a", "growth_area", "developing", None, [1])]
    assert _reasons(r) == ["no_support_level:W.2.A", INCOMPLETE]


def test_search_complete_searches_are_not_marked_incomplete():
    """Met, below the band, and not evident at the top all end the search: no search_incomplete."""
    for replies in ([[_step("W.2.A", "meets")]], [[_step("W.2.A", "growth_area", "beginning")]] * 4,
                    [[_step("W.2.A", "not_evident")]]):
        r = _run(_search(FakeChatModel({StepOut: _rounds(*replies)})))
        assert r.verdicts and r.dropped == []


def test_search_bad_evidence_entries_are_dropped_and_the_rest_kept():
    chat = FakeChatModel({StepOut: _rounds([_step(
        "W.2.A", "meets", evidence=[SegmentEvidence(segment=0, reason="x"),
                                    SegmentEvidence(segment=2, reason="good")])])})
    r = _run(_search(chat))
    assert _by_code(r)["9-10.W.2.A.a"].segments == [2] and _reasons(r) == ["bad_segment:0"]


@pytest.mark.parametrize("verdict, note, reasons", [
    ("meets", None, ["strength_without_note:W.2.A"]),
    ("meets", " ".join(["word"] * 41), ["strength_note_too_long:W.2.A"]),
    ("growth_area", "strong start", ["strength_not_met:W.2.A"]),
])
def test_search_strength_guards_keep_the_verdict(verdict, note, reasons):
    first = _step("W.2.A", verdict, "developing" if verdict == "growth_area" else None,
                  notable_strength=True, strength_note=note)
    chat = FakeChatModel({StepOut: _rounds([first], [_step("W.2.A", "meets")])})
    r = _run(_search(chat))
    assert len(r.verdicts) == 5 and not any(v.notable_strength for v in r.verdicts)
    assert _reasons(r) == reasons


def test_search_strength_sits_on_the_met_step_and_is_off_when_disabled():
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.2.A", "growth_area", "developing")],
        [_step("W.2.A", "meets", notable_strength=True, strength_note="clear topic sentences")])})
    r = _run(_search(chat))
    assert {c for c, v in _by_code(r).items() if v.notable_strength} == {"8.W.2.A.a"}
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.2.A", "meets", notable_strength=True, strength_note="x")])})
    off = _run(_search(chat), strengths=False)
    assert not any(v.notable_strength for v in off.verdicts) and off.dropped == []


def test_search_flags_levels_that_fall_as_the_grade_rises():
    chat = FakeChatModel({StepOut: _rounds(
        [_step("W.2.A", "growth_area", "approaching")], [_step("W.2.A", "growth_area", "developing")],
        [_step("W.2.A", "meets")])})
    r = _run(_search(chat))
    v = _by_code(r)
    assert (v["9-10.W.2.A.a"].support_level, v["8.W.2.A.a"].support_level) == ("approaching", "developing")
    assert [(d.stage, d.reason) for d in r.dropped] == [("band:6-G-W", "level_inversion:W.2.A")]


def test_search_daily_quota_propagates_mid_search():
    first = iter([True])

    def handler(s, u):
        if next(first, False):
            return StepOut(judgments=[_step("W.2.A", "growth_area", "developing")])
        raise DailyQuotaExceeded()

    chat = FakeChatModel({StepOut: handler})
    with pytest.raises(DailyQuotaExceeded):
        _run(_search(chat))
    assert len(chat.calls) == 2


def test_search_retries_a_failed_call_within_its_round():
    n = {"calls": 0}

    def handler(s, u):
        n["calls"] += 1
        if n["calls"] == 2:
            return StepOut.model_validate({"judgments": "not a list"})
        return StepOut(judgments=[_step(g, "growth_area" if n["calls"] == 1 else "meets", "developing")
                                  for g in _asked(u)])

    r = _run(_search(FakeChatModel({StepOut: handler})))
    assert (r.calls, r.rounds) == (3, 2) and r.dropped == []


def test_other_modes_report_zero_rounds():
    chat = FakeChatModel({PerStandardOut: lambda s, u: PerStandardOut(judgments=[_meets(c) for c in _codes(u)])})
    assert _run(_per_standard(chat)).rounds == 0


def test_v3_prompt_describes_one_step_per_group():
    text = load_prompt(PROMPTS, "progression.v3")
    for word in ("verdict", "meets", "growth_area", "not_evident", "support_level", "notable_strength",
                 "strength_note", "1 to 40 words", "discarded", "$standards", "$segments", "$conditions"):
        assert word in text
    assert "weakness" not in text and "severity" not in text


# ---- sentence segments and the per_standard.v2 evidence bar (Task S1F) ----

def test_citations_to_a_sentence_segment_are_kept_and_observed_at_sentence_level():
    sentences = ["One.", "Two.", "Three.", "Four."]
    chat = FakeChatModel({PerStandardOut: lambda s, u: PerStandardOut(judgments=[
        StandardJudgment(standard_code="3.L.1.A.a", verdict="growth_area", support_level="developing",
                         evidence=_ev(3)),
        _meets("4.L.1.A.a"), _meets("5.L.1.A.a")])})
    r = _per_standard(chat).review(sentences, "sentence", None, False)
    assert not r.dropped
    assert [(o.paragraph, o.level) for o in r.observations] == [(3, "sentence")]
    assert "[S4] Four." in chat.calls[0][2]


def test_per_standard_v2_adds_the_evidence_bar_to_v1():
    v1, v2 = load_prompt(PROMPTS, "per_standard.v1"), " ".join(load_prompt(PROMPTS, "per_standard.v2").split())
    assert "clearly shows" in v2 and "not a growth area" in v2
    assert "Absence is not evidence" in v2 and "not_evident" in v2
    assert "planning, drafting, revising" in v2 and "listed" in v2
    assert "clearly shows" not in v1  # v1 stays as it was
    for name in ("$standards", "$segments", "$assignment", "$conditions"):
        assert name in v2


def test_progression_v3_carries_the_evidence_bar_and_listed_ids():
    text = load_prompt(PROMPTS, "progression.v3")
    assert "clearly shows" in text and "Absence is not evidence" in text
    assert "listed" in text
