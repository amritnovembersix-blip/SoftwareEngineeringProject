from datetime import datetime, timezone

from agentic.lms.base import Assignment
from agentic.review.v2.mc import mc_lookup
from agentic.standards.codes import concept_of

LONG = "confuses the claim with a supporting detail and so restates the topic instead of arguing a position"


def _item(i, own, wrong_std, misconception="mixes up claim and evidence"):
    return {"id": f"q{i}", "standard": own, "choices": [
        {"key": "A", "text": "x", "correct": True},
        {"key": "B", "text": "y", "correct": False, "misconception": misconception, "standard": wrong_std},
    ]}


def _assignment(items):
    return Assignment(id="a1", name="Quiz", description="", submission_type="multiple_choice",
                      due_at=datetime(2026, 9, 1, tzinfo=timezone.utc), items=items)


ITEMS = [_item(1, "9-10.W.1.A", "9-10.W.2.A"), _item(2, "9-10.W.1.B", "9-10.W.2.A"),
         _item(3, "9-10.W.1.C", "9-10.W.2.A")]


def _ans(*choices):
    return [{"item_id": f"q{i}", "choice": c} for i, c in enumerate(choices, 1)]


def _verdict(vs, code):
    return next(v for v in vs if v.standard_code == code)


def test_single_pick_is_an_observation_only():
    vs, obs, fs = mc_lookup(_assignment(ITEMS), _ans("B", "A", "A"), 2)
    assert [(o.standard_code, o.paragraph, o.level) for o in obs] == [("9-10.W.2.A", 1, "item")]
    assert _verdict(vs, "9-10.W.2.A").verdict == "not_evident" and fs == []


def test_two_picks_on_three_items_is_a_beginning_finding():
    vs, obs, fs = mc_lookup(_assignment(ITEMS), _ans("B", "B", "A"), 2)
    v = _verdict(vs, "9-10.W.2.A")
    assert v.verdict == "growth_area" and v.support_level == "beginning"
    assert v.segments == [1, 2] and v.reviewer == "mc-lookup"
    assert abs(v.confidence - (2 / 3 + 0.2)) < 1e-9
    (f,) = fs
    assert f.standard_code == "9-10.W.2.A" and f.observation_count == 2 and f.paragraphs == [1, 2]
    assert f.support_level == "beginning" and f.severity == "beginning" and f.concept == "W.2.A"
    assert 1 <= len(f.summary.split()) <= 40


def test_min_picks_three_gives_no_finding():
    vs, obs, fs = mc_lookup(_assignment(ITEMS), _ans("B", "B", "A"), 3)
    assert fs == [] and len(obs) == 2 and _verdict(vs, "9-10.W.2.A").verdict == "not_evident"


def test_all_correct_meets():
    vs, obs, fs = mc_lookup(_assignment(ITEMS), _ans("A", "A", "A"), 2)
    assert obs == [] and fs == []
    v = _verdict(vs, "9-10.W.1.A")
    assert v.verdict == "meets" and v.confidence == 0.6
    assert _verdict(vs, "9-10.W.2.A").verdict == "not_evident"


def test_reason_is_at_most_twelve_words():
    items = [_item(1, "9-10.W.1.A", "9-10.W.2.A", LONG)]
    _, obs, _ = mc_lookup(_assignment(items), _ans("B"), 1)
    assert 1 <= len(obs[0].reason.split()) <= 12 and LONG.startswith(obs[0].reason)


def test_bad_answers_are_skipped():
    answers = [{"item_id": "nope", "choice": "A"}, {"item_id": "q1", "choice": "Z"}, {"item_id": "q2"}]
    _, obs, fs = mc_lookup(_assignment(ITEMS), answers, 1)
    assert obs == [] and fs == []


def test_long_summary_drops_whole_misconceptions():
    texts = [f"w{i}a w{i}b w{i}c w{i}d w{i}e w{i}f w{i}g w{i}h w{i}i w{i}j w{i}k w{i}l" for i in range(5)]
    items = [_item(i + 1, "9-10.W.1.A", "9-10.W.2.A", t) for i, t in enumerate(texts)]
    _, _, fs = mc_lookup(_assignment(items), _ans("B", "B", "B", "B", "B"), 2)
    parts = fs[0].summary.split("; ")
    assert len(fs[0].summary.split()) <= 40 and parts == texts[:len(parts)] and len(parts) == 3


def test_distractor_without_standard_or_misconception_is_skipped():
    item = {"id": "q1", "standard": "9-10.W.1.A", "choices": [
        {"key": "A", "text": "x", "correct": True},
        {"key": "B", "text": "y", "correct": False, "misconception": "ok", "standard": None},
        {"key": "C", "text": "z", "correct": False, "misconception": "  ", "standard": "9-10.W.2.A"}]}
    for pick in ("B", "C"):
        _, obs, fs = mc_lookup(_assignment([item]), _ans(pick), 1)
        assert obs == [] and fs == []


def test_last_answer_per_item_wins():
    answers = [{"item_id": "q1", "choice": "B"}, {"item_id": "q1", "choice": "A"}]
    _, obs, _ = mc_lookup(_assignment(ITEMS), answers, 1)
    assert obs == []
    answers = [{"item_id": "q1", "choice": "A"}, {"item_id": "q1", "choice": "B"}]
    _, obs, _ = mc_lookup(_assignment(ITEMS), answers, 1)
    assert len(obs) == 1


def test_items_counts_unanswered_items_and_untouched_standards_are_not_evident():
    vs, _, fs = mc_lookup(_assignment(ITEMS), _ans("B"), 1)
    v = _verdict(vs, "9-10.W.2.A")  # 1 pick of 3 items: developing, not a repeat
    assert v.verdict == "growth_area" and v.support_level == "developing"
    assert abs(v.confidence - (1 / 3 + 0.2)) < 1e-9
    other = _verdict(vs, "9-10.W.1.B")  # only an unanswered item
    assert other.verdict == "not_evident" and other.confidence == 0.6


# ---- counted by concept (Stage 1, Task 3) ----
def test_concept_of_is_first_four_segments():
    assert concept_of("3.R.1.B.b") == "3.R.1.B"
    assert concept_of("9-10.RL.1.A") == "9-10.RL.1.A"
    assert concept_of("3.R.1") == "3.R.1"


SAME = [_item(1, "3.R.1.A.a", "3.R.1.B.a"), _item(2, "3.R.1.A.b", "3.R.1.B.b"),
        _item(3, "3.R.1.A.c", "3.R.1.A.c")]


def test_two_picks_on_different_standards_of_one_concept_is_a_growth_area():
    vs, obs, fs = mc_lookup(_assignment(SAME[:2]), _ans("B", "B"), 2)
    assert _verdict(vs, "3.R.1.B.a").verdict == "growth_area"
    assert _verdict(vs, "3.R.1.B.b").verdict == "growth_area"
    assert _verdict(vs, "3.R.1.B.a").support_level == "beginning"
    assert _verdict(vs, "3.R.1.A.a").verdict == "not_evident"
    (f,) = fs
    assert f.standard_code == "3.R.1.B.a"  # tie: lowest code
    assert f.observation_count == 2 and f.paragraphs == [1, 2] and len(f.reasons) == 2
    assert len(obs) == 2


def test_most_picked_standard_gets_the_finding_and_unpicked_asked_is_not_evident():
    items = [_item(1, "3.R.1.A.a", "3.R.1.B.b"), _item(2, "3.R.1.A.b", "3.R.1.B.b"),
             _item(3, "3.R.1.B.a", "3.R.1.B.a")]
    vs, _, fs = mc_lookup(_assignment(items), _ans("B", "B", "A"), 2)
    (f,) = fs
    assert f.standard_code == "3.R.1.B.b" and f.observation_count == 2
    assert _verdict(vs, "3.R.1.B.b").verdict == "growth_area"
    assert _verdict(vs, "3.R.1.B.a").verdict == "not_evident"  # asked, not picked


def test_one_pick_in_a_concept_is_an_observation_only():
    vs, obs, fs = mc_lookup(_assignment(SAME[:2]), _ans("B", "A"), 2)
    assert len(obs) == 1 and fs == []
    assert _verdict(vs, "3.R.1.B.a").verdict == "not_evident"


def test_threshold_setting_is_respected_across_a_concept():
    _, _, fs = mc_lookup(_assignment(SAME[:2]), _ans("B", "B"), 3)
    assert fs == []
    _, _, fs = mc_lookup(_assignment(SAME[:2]), _ans("B", "B"), 1)
    assert len(fs) == 1


def test_meets_gives_every_asked_standard_in_the_concept_meets():
    vs, _, fs = mc_lookup(_assignment(SAME[:2]), _ans("A", "A"), 2)
    assert fs == []
    assert _verdict(vs, "3.R.1.A.a").verdict == "meets" and _verdict(vs, "3.R.1.A.b").verdict == "meets"


def test_unanswered_item_in_concept_blocks_meets():
    vs, _, _ = mc_lookup(_assignment(SAME[:2]), _ans("A"), 2)
    assert _verdict(vs, "3.R.1.A.a").verdict == "not_evident"


def test_ten_items_three_concepts():
    # concepts X (4 items), Y (3 items), Z (3 items); distractors of X-items point at Y and Z
    items = []
    for i in range(1, 5):
        items.append(_item(i, f"3.R.1.A.{'abcd'[i-1]}", "3.R.1.B.a" if i <= 2 else "3.R.1.C.a"))
    for i in range(5, 8):
        items.append(_item(i, f"3.R.1.B.{'bcd'[i-5]}", "3.R.1.B.b"))
    for i in range(8, 11):
        items.append(_item(i, f"3.R.1.C.{'bcd'[i-8]}", "3.R.1.C.b"))
    # X-items 1,2 picked (Y x2); item 3 picked (Z x1); items 5-7 correct; 8-10 correct; 4 correct
    vs, obs, fs = mc_lookup(_assignment(items), _ans("B", "B", "B", "A", "A", "A", "A", "A", "A", "A"), 2)
    assert len(obs) == 3
    (f,) = fs
    assert f.standard_code == "3.R.1.B.a" and f.observation_count == 2
    # Y touched by items 1, 2 (distractor) and 5-7 (own) = 5 items, 2 picks: developing
    assert _verdict(vs, "3.R.1.B.a").support_level == "developing"
    assert _verdict(vs, "3.R.1.C.a").verdict == "not_evident"  # one pick only
    assert _verdict(vs, "3.R.1.A.a").verdict == "not_evident"  # picks elsewhere do not meet X (items 1-3 wrong)
    assert _verdict(vs, "3.R.1.C.b").verdict == "not_evident"  # Z has a pick, so not meets
